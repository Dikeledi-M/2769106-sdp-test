"""Metrics over a commit set: file, directory, repository, and author.

Definitions
-----------
Let ``H`` be a commit set: a subset of the non-merge commits reachable from a
reference (typically HEAD), optionally narrowed by a committer-date range, an
explicit commit list, or a set of authors. For every object ``o`` - a file in
``H[F]`` or a directory in ``H[D]`` (the root is the empty path ``""``) - the
metrics are sums of the per-commit diff values over ``H``:

* file metrics: ``l+`` added lines, ``l-`` removed lines, growth
  ``delta = l+ - l-``, churn ``lambda = l+ + l-``;
* directory metrics: the same four values rolled up recursively over all
  immediate child files and subdirectories (equivalent to accumulating each
  changed file into every ancestor directory);
* commit set metrics: modifications ``n`` (commits in ``H`` with
  ``lambda > 0`` on ``o``), modification frequency ``eta = n / |H|`` and
  churn rate ``rho = lambda / |H|`` (zero for an empty commit set);
* author metrics: per author ``a`` - modifications, churn and ownership
  ``omega = lambda_a / lambda`` for the scoped object (zero when the object
  has no churn).

Repository metrics are the directory metrics of the repository root.

Rename handling comes for free: the ingestion stores every diff entry on its
new path (``--find-renames=50%``), so a pure rename contributes ``0/0`` (zero
churn, never a modification) and a rename combined with edits contributes
only the edits, attributed to the new path. A deletion is stored as an entry
on the deleted path and therefore counts as removed lines there. Binary files
are excluded (git's own binary detection, recorded as ``is_binary`` at
ingestion time).

Scope notes
-----------
* Only objects with at least one diff row in the commit set are listed. Any
  other object contributes ``0/0/0/0`` to every metric, so listing presence
  follows change events; full trees are never materialised. Authors are
  listed per object with the same rule (no diff rows in scope, no entry).
* An optional ``path`` narrows the reported scope: files below ``p``
  (``path == p`` or ``path.startswith(p + "/")``); directories are reported
  within the same scope, i.e. ancestors above ``p`` are dropped. Author
  metrics scope to the object itself (``p``) or the subtree below it.
* Datetimes are normalised to UTC before querying, matching the storage
  format written by the ingestion service.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.author import Author
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
    modifications: int
    modification_frequency: float
    churn_rate: float


@dataclass(frozen=True)
class AuthorMetrics:
    """Per-author metrics on one object within the commit set."""

    author_id: int
    display_name: str
    modifications: int
    churn: int
    ownership: float


@dataclass
class MetricsResult:
    """Aggregated metrics of a commit set."""

    commit_count: int
    objects: list[ObjectMetrics] = field(default_factory=list)


@dataclass
class RepositoryMetricsResult:
    """Repository metrics: the root directory of the commit tree."""

    commit_count: int
    root: ObjectMetrics


@dataclass
class AuthorMetricsResult:
    """Author metrics for one object (a file, a directory, or the root)."""

    commit_count: int
    path: str
    authors: list[AuthorMetrics] = field(default_factory=list)


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


_ChangeRow = tuple[int, str, int, int]  # commit_id, path, added lines, removed lines


def _collect_rows(db: Session, commit_ids: list[int], path: str | None) -> list[_ChangeRow]:
    """Changed non-binary files as ``(commit_id, path, added, removed)`` rows.

    There is at most one diff entry per path per commit, so each row is one
    ``(h, o)`` pair of the spec - the granularity needed for modifications
    (``lambda_h,o > 0``) and per-author attribution.
    """
    rows: list[_ChangeRow] = []
    if not commit_ids:
        return rows

    conditions = [CommitFile.is_binary.is_(False)]
    if path is not None:
        conditions.append(
            or_(CommitFile.path == path, CommitFile.path.startswith(path + "/"))
        )

    for start in range(0, len(commit_ids), _QUERY_BATCH):
        batch = commit_ids[start : start + _QUERY_BATCH]
        statement = (
            select(
                CommitFile.commit_id,
                CommitFile.path,
                func.sum(func.coalesce(CommitFile.insertions, 0)),
                func.sum(func.coalesce(CommitFile.deletions, 0)),
            )
            .where(CommitFile.commit_id.in_(batch), *conditions)
            .group_by(CommitFile.commit_id, CommitFile.path)
        )
        rows.extend(
            (int(commit_id), file_path, int(added), int(removed))
            for commit_id, file_path, added, removed in db.execute(statement)
        )
    return rows


def _file_totals(rows: list[_ChangeRow]) -> dict[str, tuple[int, int]]:
    """Sum ``(added, removed)`` per changed file path."""
    totals: dict[str, list[int]] = {}
    for _, file_path, added, removed in rows:
        accumulator = totals.setdefault(file_path, [0, 0])
        accumulator[0] += added
        accumulator[1] += removed
    return {file_path: (added, removed) for file_path, (added, removed) in totals.items()}


def _file_modifications(rows: list[_ChangeRow]) -> dict[str, int]:
    """Commits with ``lambda_h,f > 0`` per file (``Gamma(h, f) = 1``)."""
    counts: dict[str, int] = {}
    for _, file_path, added, removed in rows:
        if added + removed > 0:
            counts[file_path] = counts.get(file_path, 0) + 1
    return counts


def _ancestor_dirs(path: str) -> list[str]:
    """Directories containing ``path``, deepest first; root ``""`` last."""
    parts = path.split("/")
    directories = ["/".join(parts[:index]) for index in range(len(parts) - 1, 0, -1)]
    directories.append("")
    return directories


def _in_scope(directory: str, scope: str | None) -> bool:
    """Whether a directory lies at or below the ``path`` filter scope."""
    if scope is None:
        return True
    return directory == scope or directory.startswith(scope + "/")


def _directory_totals(
    file_totals: dict[str, tuple[int, int]], scope: str | None
) -> dict[str, tuple[int, int]]:
    """Roll the file totals up into every ancestor directory (root last)."""
    totals: dict[str, list[int]] = {}
    for file_path, (added, removed) in file_totals.items():
        for directory in _ancestor_dirs(file_path):
            if not _in_scope(directory, scope):
                continue
            accumulator = totals.setdefault(directory, [0, 0])
            accumulator[0] += added
            accumulator[1] += removed
    return {directory: (added, removed) for directory, (added, removed) in totals.items()}


def _directory_modifications(rows: list[_ChangeRow], scope: str | None) -> dict[str, int]:
    """Commits with ``lambda_h,d > 0`` per directory (``Gamma(h, d) = 1``).

    A directory counts a commit once, no matter how many files below it
    changed, so the per-commit churn is aggregated before counting.
    """
    per_commit: dict[int, dict[str, int]] = {}
    for commit_id, file_path, added, removed in rows:
        churn = added + removed
        if churn == 0:
            continue
        directories = per_commit.setdefault(commit_id, {})
        for directory in _ancestor_dirs(file_path):
            if _in_scope(directory, scope):
                directories[directory] = directories.get(directory, 0) + churn

    counts: dict[str, int] = {}
    for directories in per_commit.values():
        for directory in directories:
            counts[directory] = counts.get(directory, 0) + 1
    return counts


def _to_objects(
    totals: dict[str, tuple[int, int]],
    modifications: dict[str, int],
    commit_count: int,
) -> list[ObjectMetrics]:
    objects = []
    for path, (added, removed) in totals.items():
        churn = added + removed
        count = modifications.get(path, 0)
        objects.append(
            ObjectMetrics(
                path=path,
                added_lines=added,
                removed_lines=removed,
                growth=added - removed,
                churn=churn,
                modifications=count,
                modification_frequency=(count / commit_count) if commit_count else 0.0,
                churn_rate=(churn / commit_count) if commit_count else 0.0,
            )
        )
    objects.sort(key=lambda item: item.path)
    return objects


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def file_metrics(
    db: Session, repository: Repository, filters: MetricsFilter | None = None
) -> MetricsResult:
    """File metrics: lines, growth, churn, modifications, rates."""
    filters = filters or MetricsFilter()
    commit_ids = resolve_commit_ids(db, repository, filters)
    rows = _collect_rows(db, commit_ids, filters.normalized_path)
    return MetricsResult(
        commit_count=len(commit_ids),
        objects=_to_objects(_file_totals(rows), _file_modifications(rows), len(commit_ids)),
    )


def directory_metrics(
    db: Session, repository: Repository, filters: MetricsFilter | None = None
) -> MetricsResult:
    """Directory metrics: the file values rolled up into every ancestor."""
    filters = filters or MetricsFilter()
    scope = filters.normalized_path
    commit_ids = resolve_commit_ids(db, repository, filters)
    rows = _collect_rows(db, commit_ids, scope)
    return MetricsResult(
        commit_count=len(commit_ids),
        objects=_to_objects(
            _directory_totals(_file_totals(rows), scope),
            _directory_modifications(rows, scope),
            len(commit_ids),
        ),
    )


def repository_metrics(
    db: Session, repository: Repository, filters: MetricsFilter | None = None
) -> RepositoryMetricsResult:
    """Repository metrics: directory metrics on the root of the commit tree."""
    filters = filters or MetricsFilter()
    if filters.path is not None:  # the root is fixed; a path scope does not apply
        filters = replace(filters, path=None)
    commit_ids = resolve_commit_ids(db, repository, filters)
    rows = _collect_rows(db, commit_ids, None)
    objects = _to_objects(
        _directory_totals(_file_totals(rows), None),
        _directory_modifications(rows, None),
        len(commit_ids),
    )
    root = next(
        (item for item in objects if item.path == ""),
        ObjectMetrics(
            path="",
            added_lines=0,
            removed_lines=0,
            growth=0,
            churn=0,
            modifications=0,
            modification_frequency=0.0,
            churn_rate=0.0,
        ),
    )
    return RepositoryMetricsResult(commit_count=len(commit_ids), root=root)


def author_metrics(
    db: Session, repository: Repository, filters: MetricsFilter | None = None
) -> AuthorMetricsResult:
    """Author modifications, churn and ownership for the scoped object.

    The scope defaults to the repository root; ``path`` selects a file or a
    directory instead. Ownership is the author's share of the object's churn
    (``lambda_a / lambda``), zero when the object has no churn.
    """
    filters = filters or MetricsFilter()
    scope = filters.normalized_path
    commit_ids = resolve_commit_ids(db, repository, filters)

    commit_author = _commit_authors(db, commit_ids)
    commit_churn: dict[int, int] = {}
    for commit_id, _, added, removed in _collect_rows(db, commit_ids, scope):
        if commit_id in commit_author:
            commit_churn[commit_id] = commit_churn.get(commit_id, 0) + added + removed

    churn_by_author: dict[int, int] = {}
    modifications_by_author: dict[int, int] = {}
    for commit_id, churn in commit_churn.items():
        author_id = commit_author[commit_id]
        churn_by_author[author_id] = churn_by_author.get(author_id, 0) + churn
        if churn > 0:
            modifications_by_author[author_id] = modifications_by_author.get(author_id, 0) + 1

    total_churn = sum(churn_by_author.values())
    names = _author_names(db, set(churn_by_author))
    authors = [
        AuthorMetrics(
            author_id=author_id,
            display_name=names.get(author_id, f"author #{author_id}"),
            modifications=modifications_by_author.get(author_id, 0),
            churn=churn,
            ownership=(churn / total_churn) if total_churn else 0.0,
        )
        for author_id, churn in churn_by_author.items()
    ]
    authors.sort(key=lambda item: (-item.churn, item.display_name))
    return AuthorMetricsResult(commit_count=len(commit_ids), path=scope or "", authors=authors)


def _commit_authors(db: Session, commit_ids: list[int]) -> dict[int, int]:
    """Canonical author id per commit id (after author merging)."""
    authors: dict[int, int] = {}
    for start in range(0, len(commit_ids), _QUERY_BATCH):
        batch = commit_ids[start : start + _QUERY_BATCH]
        statement = select(Commit.id, Commit.author_id).where(
            Commit.id.in_(batch), Commit.author_id.is_not(None)
        )
        authors.update(
            {int(commit_id): int(author_id) for commit_id, author_id in db.execute(statement)}
        )
    return authors


def _author_names(db: Session, author_ids: set[int]) -> dict[int, str]:
    names: dict[int, str] = {}
    if not author_ids:
        return names
    ids = list(author_ids)
    for start in range(0, len(ids), _QUERY_BATCH):
        batch = ids[start : start + _QUERY_BATCH]
        statement = select(Author.id, Author.display_name).where(Author.id.in_(batch))
        names.update({int(author_id): name for author_id, name in db.execute(statement)})
    return names
