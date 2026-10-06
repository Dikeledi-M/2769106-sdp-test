"""File and directory metrics over a commit set.

Definitions
-----------
Let ``H`` be a commit set: a subset of the non-merge commits reachable from
a reference (typically HEAD), optionally narrowed by a committer-date range,
an explicit commit list, or a set of authors. For every object (file or
directory) the metrics are sums of the per-commit diff values of ``H``:

* ``l+``  file added lines, ``l-`` file removed lines;
* growth ``delta = l+ - l-``, churn ``lambda = l+ + l-``.

These map onto :class:`ObjectMetrics`: ``added_lines``/``removed_lines`` are
``l+``/``l-``, ``growth`` is ``delta`` and ``churn`` is ``lambda``.

Rename handling comes for free: the ingestion stores every diff entry on its
new path (``--find-renames=50%``), so a pure rename contributes ``0/0`` and a
rename combined with edits contributes only the edits, attributed to the new
path. A deletion is stored as an entry on the deleted path and therefore
counts as removed lines there. Binary files are excluded (git's own binary
detection, recorded as ``is_binary`` at ingestion time).

Directory metrics roll the same totals up: each changed file accumulates into
every one of its ancestor directories, which is equivalent to the recursive
"immediate objects" definition. The repository root is the empty path ``""``.

Scope notes
-----------
* Only objects with at least one diff row in the commit set are listed. Any
  other object contributes ``0/0/0/0`` to every metric, so listing presence
  follows change events; full trees are never materialised.
* An optional ``path`` narrows the reported scope: files below ``p``
  (``path == p`` or ``path.startswith(p + "/")``); directories are reported
  within the same scope, i.e. ancestors above ``p`` are dropped.
* Datetimes are normalised to UTC before querying, matching the storage
  format written by the ingestion service.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.commit import Commit, CommitFile
from app.models.repository import Repository

_QUERY_BATCH = 500


class MetricsError(Exception):
    """Raised when a metrics query cannot be answered (e.g. unknown reference)."""


@dataclass(frozen=True)
class MetricsFilter:
    """Commit-set scope of a metrics query.

    ``from_ts`` is inclusive and ``to_ts`` exclusive, both on the committer
    date. ``commit_shas`` is an explicit commit set, intersected with the
    other filters. ``reference_sha`` restricts to commits reachable from that
    commit; the default (``None``) means everything ingested.
    """

    from_ts: datetime | None = None
    to_ts: datetime | None = None
    commit_shas: tuple[str, ...] = ()
    author_ids: tuple[int, ...] = ()
    reference_sha: str | None = None
    path: str | None = None

    @property
    def normalized_path(self) -> str | None:
        """The path filter without surrounding slashes (``None`` = no filter)."""
        if self.path is None:
            return None
        cleaned = self.path.strip("/")
        return cleaned or None


@dataclass(frozen=True)
class ObjectMetrics:
    """Metrics of a single object (file or directory) within the commit set."""

    path: str
    added_lines: int
    removed_lines: int
    growth: int
    churn: int


@dataclass
class MetricsResult:
    """Aggregated metrics of a commit set."""

    commit_count: int
    objects: list[ObjectMetrics] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Commit set resolution
# ---------------------------------------------------------------------------


def _normalize_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _reachable_from(db: Session, repository_id: int, reference_sha: str) -> set[str]:
    """SHAs reachable from ``reference_sha``, walking parents (merges included).

    The stored history contains every commit reachable from HEAD, so the
    reference may be any of them (typically HEAD itself).
    """
    rows = db.execute(
        select(Commit.sha, Commit.parent_shas).where(Commit.repository_id == repository_id)
    ).all()
    graph = {sha: parents or [] for sha, parents in rows}
    if reference_sha not in graph:
        raise MetricsError(f"reference commit not found: {reference_sha}")

    reachable: set[str] = set()
    pending = [reference_sha]
    while pending:
        sha = pending.pop()
        if sha in reachable:
            continue
        reachable.add(sha)
        pending.extend(
            parent for parent in graph[sha] if parent not in reachable and parent in graph
        )
    return reachable


def resolve_commit_ids(
    db: Session, repository: Repository, filters: MetricsFilter
) -> list[int]:
    """Resolve the commit set ``H`` to the ids of the stored commits."""
    statement = select(Commit.id, Commit.sha).where(
        Commit.repository_id == repository.id,
        Commit.is_merge.is_(False),  # H-bar excludes merge commits
    )

    from_ts = _normalize_utc(filters.from_ts)
    to_ts = _normalize_utc(filters.to_ts)
    if from_ts is not None:
        statement = statement.where(Commit.committed_at >= from_ts)
    if to_ts is not None:
        statement = statement.where(Commit.committed_at < to_ts)
    if filters.author_ids:
        statement = statement.where(Commit.author_id.in_(filters.author_ids))
    if filters.commit_shas:
        statement = statement.where(Commit.sha.in_(filters.commit_shas))

    rows = db.execute(statement).all()

    if filters.reference_sha:
        reachable = _reachable_from(db, repository.id, filters.reference_sha)
        rows = [row for row in rows if row.sha in reachable]

    return [row.id for row in rows]


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _aggregate_files(
    db: Session, commit_ids: list[int], path: str | None
) -> dict[str, tuple[int, int]]:
    """Sum (added, removed) per changed file path over the given commits."""
    if not commit_ids:
        return {}

    conditions = [CommitFile.is_binary.is_(False)]
    if path is not None:
        conditions.append(
            or_(CommitFile.path == path, CommitFile.path.startswith(path + "/"))
        )

    totals: dict[str, list[int]] = {}
    for start in range(0, len(commit_ids), _QUERY_BATCH):
        batch = commit_ids[start : start + _QUERY_BATCH]
        statement = (
            select(
                CommitFile.path,
                func.coalesce(func.sum(CommitFile.insertions), 0),
                func.coalesce(func.sum(CommitFile.deletions), 0),
            )
            .where(CommitFile.commit_id.in_(batch), *conditions)
            .group_by(CommitFile.path)
        )
        for file_path, added, removed in db.execute(statement):
            accumulator = totals.setdefault(file_path, [0, 0])
            accumulator[0] += int(added)
            accumulator[1] += int(removed)

    return {file_path: (added, removed) for file_path, (added, removed) in totals.items()}


def _ancestor_dirs(path: str) -> list[str]:
    """Directories containing ``path``, deepest first; root ``""`` last."""
    parts = path.split("/")
    directories = ["/".join(parts[:index]) for index in range(len(parts) - 1, 0, -1)]
    directories.append("")
    return directories


def _to_objects(totals: dict[str, tuple[int, int]]) -> list[ObjectMetrics]:
    objects = [
        ObjectMetrics(
            path=path,
            added_lines=added,
            removed_lines=removed,
            growth=added - removed,
            churn=added + removed,
        )
        for path, (added, removed) in totals.items()
    ]
    objects.sort(key=lambda item: item.path)
    return objects


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def file_metrics(
    db: Session, repository: Repository, filters: MetricsFilter | None = None
) -> MetricsResult:
    """File metrics (added, removed, growth, churn) over the commit set."""
    filters = filters or MetricsFilter()
    commit_ids = resolve_commit_ids(db, repository, filters)
    totals = _aggregate_files(db, commit_ids, filters.normalized_path)
    return MetricsResult(commit_count=len(commit_ids), objects=_to_objects(totals))


def directory_metrics(
    db: Session, repository: Repository, filters: MetricsFilter | None = None
) -> MetricsResult:
    """Directory metrics: the file totals rolled up into every ancestor."""
    filters = filters or MetricsFilter()
    commit_ids = resolve_commit_ids(db, repository, filters)
    scope = filters.normalized_path
    file_totals = _aggregate_files(db, commit_ids, scope)

    directory_totals: dict[str, list[int]] = {}
    for file_path, (added, removed) in file_totals.items():
        for directory in _ancestor_dirs(file_path):
            if scope is not None and not (
                directory == scope or directory.startswith(scope + "/")
            ):
                continue
            accumulator = directory_totals.setdefault(directory, [0, 0])
            accumulator[0] += added
            accumulator[1] += removed

    totals = {directory: (added, removed) for directory, (added, removed) in directory_totals.items()}
    return MetricsResult(commit_count=len(commit_ids), objects=_to_objects(totals))
