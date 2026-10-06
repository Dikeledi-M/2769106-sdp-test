"""Git repository ingestion.

Walks the history reachable from a reference (HEAD) and persists commits,
per-file diff statistics, and raw author identities into the database.

Implementation notes
--------------------
* A single ``git log --numstat --raw -z`` pass collects everything:
  the format block (SHA, parents, dates, author/committer, subject),
  ``--raw`` entries (change status letters ``A/M/D/R/C/T`` plus rename
  paths) and ``--numstat`` entries (per-file added/removed line counts).
* Paths are NUL-delimited (``-z``), so there is no quoting or brace
  compression ambiguity (``dir/{a => b}.txt``). Rename detection runs at the
  required 50% threshold (``--find-renames=50%``): a pure rename produces a
  ``0/-0`` entry on the new path, and changes combined with a rename are
  reported on the new path only.
* Binary files (git's own detection) are reported as ``-/-`` and stored with
  NULL line counts and ``is_binary=True``; the metrics layer excludes them.
* Merge commits are stored (``is_merge=True``) with no file rows. Metrics
  exclude them, matching the definition of H-bar.
* Only the subject line of each commit message is stored.
* All datetimes are normalized to UTC before persisting, so comparisons in
  both SQLite (naive UTC strings) and PostgreSQL (timestamptz) stay correct.

Re-ingestion is idempotent: commit rows are replaced, while canonical
authors and their identities are preserved so manual merges made between
two ingestion runs are never lost.
"""
from __future__ import annotations

import subprocess
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.author import Author, AuthorIdentity, IdentitySource
from app.models.commit import Commit, CommitFile
from app.models.repository import Repository, RepositoryStatus

# Record/field separators used inside the --format string; both are control
# characters that cannot appear in any of the referenced git fields.
_RECORD_SEP = "\x1e"
_FIELD_SEP = "\x1f"
_NUL = b"\x00"

_LOG_FORMAT = (
    f"%x1e%H{_FIELD_SEP}%P{_FIELD_SEP}%aI{_FIELD_SEP}%cI{_FIELD_SEP}"
    f"%an{_FIELD_SEP}%ae{_FIELD_SEP}%cn{_FIELD_SEP}%ce{_FIELD_SEP}%s"
)

_INSERT_BATCH = 500


class IngestionError(Exception):
    """Raised when a repository cannot be ingested."""


@dataclass
class IngestionStats:
    commit_count: int
    author_count: int
    head_sha: str
    default_branch: str | None


@dataclass
class ParsedFileChange:
    """One file entry of a commit diff."""

    path: str
    old_path: str | None
    change_type: str  # A=added, M=modified, D=deleted, R=renamed, C=copied, T=typechange
    insertions: int | None  # None for binary files
    deletions: int | None
    is_binary: bool


@dataclass
class ParsedCommit:
    sha: str
    parent_shas: list[str]
    authored_at: datetime | None
    committed_at: datetime | None
    author_name: str | None
    author_email: str | None
    committer_name: str | None
    committer_email: str | None
    subject: str | None
    changes: list[ParsedFileChange] = field(default_factory=list)

    @property
    def is_merge(self) -> bool:
        return len(self.parent_shas) > 1


# ---------------------------------------------------------------------------
# git plumbing
# ---------------------------------------------------------------------------


def _git(repo_path: Path, *args: str) -> bytes:
    try:
        process = subprocess.run(
            # safe.directory: the stored archive / clone may sit on a mounted
            # volume owned by another user (e.g. Docker); this is a read-only
            # analysis tool, so the guard is intentionally lifted.
            ["git", "-C", str(repo_path), "-c", "safe.directory=*", *args],
            capture_output=True,
        )
    except FileNotFoundError as exc:  # pragma: no cover - environment issue
        raise IngestionError("git executable not found") from exc
    if process.returncode != 0:
        message = process.stderr.decode("utf-8", errors="replace").strip()
        raise IngestionError(message or f"git {' '.join(args)} failed")
    return process.stdout


def _parse_git_datetime(raw: bytes) -> datetime | None:
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return None
    value = datetime.fromisoformat(text)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _decode(raw: bytes) -> str | None:
    if not raw:
        return None
    return raw.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Parsing of the git log output
# ---------------------------------------------------------------------------


def _parse_payload(
    payload: bytes,
) -> tuple[list[tuple[str, list[bytes]]], list[tuple[bytes, bytes, list[bytes]]]]:
    """Split a commit's diff payload into raw and numstat entries.

    The ``--raw`` block always comes first, followed by the ``--numstat``
    block, both in the same per-file order. Entries are NUL-delimited:

    * raw:     ``:100644 100644 <sha> <sha> M\\0<path>\\0``
               ``:100644 100644 <sha> <sha> R054\\0<old>\\0<new>\\0``
    * numstat: ``12\\t3\\t<path>\\0`` (or ``\\t\\0<old>\\0<new>\\0`` when renamed,
               ``-\\t-\\t<path>\\0`` for binary files)
    """
    chunks = payload.split(_NUL)
    raw_entries: list[tuple[str, list[bytes]]] = []
    numstat_entries: list[tuple[bytes, bytes, list[bytes]]] = []
    index = 0
    total = len(chunks)

    # Phase 1: raw entries (status letter + path(s)).
    while index < total:
        chunk = chunks[index]
        if not chunk:
            index += 1
            continue
        if not chunk.startswith(b":"):
            break
        status = chunk.rsplit(b" ", 1)[-1].decode("ascii", "replace")
        letter = status[:1]
        path_count = 2 if letter in ("R", "C") else 1
        index += 1
        paths = []
        for _ in range(path_count):
            paths.append(chunks[index])
            index += 1
        raw_entries.append((letter, paths))

    # Phase 2: numstat entries (line counts + path(s)).
    while index < total:
        chunk = chunks[index]
        if not chunk:
            index += 1
            continue
        parts = chunk.split(b"\t")
        added_raw = parts[0]
        removed_raw = parts[1] if len(parts) > 1 else b""
        if len(parts) >= 3 and parts[2] != b"":
            paths = [b"\t".join(parts[2:])]
            index += 1
        else:  # rename form: counts chunk, then old path, then new path
            paths = [chunks[index + 1], chunks[index + 2]]
            index += 3
        numstat_entries.append((added_raw, removed_raw, paths))

    return raw_entries, numstat_entries


def _merge_changes(
    raw_entries: list[tuple[str, list[bytes]]],
    numstat_entries: list[tuple[bytes, bytes, list[bytes]]],
) -> list[ParsedFileChange]:
    changes: list[ParsedFileChange] = []

    if len(raw_entries) == len(numstat_entries):
        for (letter, raw_paths), (added_raw, removed_raw, numstat_paths) in zip(
            raw_entries, numstat_entries
        ):
            is_binary = added_raw == b"-"
            old_path = raw_paths[0] if letter in ("R", "C") and len(raw_paths) == 2 else None
            path = raw_paths[-1]
            changes.append(
                ParsedFileChange(
                    path=_decode(path) or _decode(numstat_paths[-1]) or "",
                    old_path=_decode(old_path),
                    change_type=letter or "M",
                    insertions=None if is_binary else int(added_raw),
                    deletions=None if is_binary else int(removed_raw),
                    is_binary=is_binary,
                )
            )
        return changes

    # Defensive fallback: pair only with numstat data and classify heuristically.
    for added_raw, removed_raw, paths in numstat_entries:
        is_binary = added_raw == b"-"
        added = None if is_binary else int(added_raw)
        removed = None if is_binary else int(removed_raw)
        if len(paths) == 2:
            change_type = "R"
            old_path, path = paths[0], paths[1]
        else:
            change_type = (
                "A" if removed == 0 else "D" if added == 0 else "M"
            )
            old_path, path = None, paths[0]
        changes.append(
            ParsedFileChange(
                path=_decode(path) or "",
                old_path=_decode(old_path),
                change_type=change_type,
                insertions=added,
                deletions=removed,
                is_binary=is_binary,
            )
        )
    return changes


def _iter_parsed_commits(output: bytes) -> Iterator[ParsedCommit]:
    record_sep = _RECORD_SEP.encode("ascii")
    field_sep = _FIELD_SEP.encode("ascii")

    for record in output.split(record_sep)[1:]:
        header, _, payload = record.partition(_NUL)
        fields = header.split(field_sep)
        if len(fields) != 9:
            continue
        (
            sha_raw,
            parents_raw,
            authored_raw,
            committed_raw,
            author_name,
            author_email,
            committer_name,
            committer_email,
            subject,
        ) = fields

        # The diff section starts with a newline; merge commits have none.
        payload = payload.lstrip(b"\n")
        raw_entries, numstat_entries = _parse_payload(payload)

        yield ParsedCommit(
            sha=sha_raw.decode("ascii", "replace"),
            parent_shas=parents_raw.decode("ascii", "replace").split(),
            authored_at=_parse_git_datetime(authored_raw),
            committed_at=_parse_git_datetime(committed_raw),
            author_name=_decode(author_name),
            author_email=_decode(author_email),
            committer_name=_decode(committer_name),
            committer_email=_decode(committer_email),
            subject=_decode(subject),
            changes=_merge_changes(raw_entries, numstat_entries),
        )


def parse_git_log(output: bytes) -> list[ParsedCommit]:
    """Parse the output of the ingestion ``git log`` command (public for tests)."""
    return list(_iter_parsed_commits(output))


# ---------------------------------------------------------------------------
# Identity resolution
# ---------------------------------------------------------------------------


class _IdentityResolver:
    """Maps raw (name, email) pairs to canonical authors, creating as needed.

    Authors are grouped by lower-cased email (automatic merging); every
    distinct (name, email) pair is stored as its own identity row attached
    to the same author, so display-name variations stay traceable.
    """

    def __init__(self, db: Session, repository: Repository) -> None:
        self._db = db
        self._repository_id = repository.id
        self._authors_by_id: dict[int, Author] = {}
        self._authors_by_email: dict[str, Author] = {}
        self._identities: dict[tuple[str, str | None], AuthorIdentity] = {}

        authors = db.execute(
            select(Author).where(Author.repository_id == repository.id)
        ).scalars()
        for author in authors:
            self._authors_by_id[author.id] = author
            if author.primary_email:
                self._authors_by_email[author.primary_email.lower()] = author

        identities = db.execute(
            select(AuthorIdentity).where(AuthorIdentity.repository_id == repository.id)
        ).scalars()
        for identity in identities:
            self._identities[(identity.email, identity.name)] = identity
            author = self._authors_by_id.get(identity.author_id)
            if author is not None:
                self._authors_by_email.setdefault(identity.email, author)

    def resolve(self, name: str | None, email: str | None) -> tuple[int, int]:
        """Return ``(author_id, identity_id)`` for a raw git identity."""
        email_key = (email or "").strip().lower()
        key = (email_key, name)
        identity = self._identities.get(key)
        if identity is not None:
            return identity.author_id, identity.id

        author = self._authors_by_email.get(email_key)
        if author is None:
            author = Author(
                repository_id=self._repository_id,
                display_name=name or email_key or "unknown",
                primary_email=email_key or None,
            )
            self._db.add(author)
            self._db.flush()
            self._authors_by_id[author.id] = author
            if email_key:
                self._authors_by_email[email_key] = author

        identity = AuthorIdentity(
            author_id=author.id,
            repository_id=self._repository_id,
            name=name,
            email=email_key,
            source=IdentitySource.AUTO,
        )
        self._db.add(identity)
        self._db.flush()
        self._identities[key] = identity
        return author.id, identity.id

    @property
    def author_count(self) -> int:
        return len({identity.author_id for identity in self._identities.values()})


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def ingest_repository(db: Session, repository: Repository, repo_path: Path) -> IngestionStats:
    """Ingest the history reachable from HEAD of ``repo_path``.

    Replaces any previously stored commits for the repository while keeping
    authors and identities intact. On failure the repository is marked
    ``ERROR`` and an :class:`IngestionError` is raised.
    """
    repo_path = Path(repo_path)
    repository.status = RepositoryStatus.INGESTING
    db.commit()

    try:
        if not repo_path.exists():
            raise IngestionError(f"path does not exist: {repo_path}")

        _git(repo_path, "rev-parse", "--git-dir")
        try:
            head_sha = _git(repo_path, "rev-parse", "HEAD").decode("ascii").strip()
        except IngestionError as exc:
            raise IngestionError("repository has no commits") from exc
        branch = _git(repo_path, "rev-parse", "--abbrev-ref", "HEAD").decode("ascii").strip()
        default_branch = branch if branch and branch != "HEAD" else None

        output = _git(
            repo_path,
            "log",
            "--numstat",
            "--raw",
            "-z",
            "--find-renames=50%",
            f"--format={_LOG_FORMAT}",
        )

        # Idempotent re-ingestion: replace commit rows, preserve author merges.
        db.execute(delete(CommitFile).where(CommitFile.repository_id == repository.id))
        db.execute(delete(Commit).where(Commit.repository_id == repository.id))
        db.flush()

        resolver = _IdentityResolver(db, repository)
        processed = 0
        for parsed in _iter_parsed_commits(output):
            author_id, identity_id = resolver.resolve(parsed.author_name, parsed.author_email)
            commit = Commit(
                repository_id=repository.id,
                sha=parsed.sha,
                author_id=author_id,
                author_identity_id=identity_id,
                author_name=parsed.author_name,
                author_email=parsed.author_email,
                authored_at=parsed.authored_at,
                committer_name=parsed.committer_name,
                committer_email=parsed.committer_email,
                committed_at=parsed.committed_at,
                message=parsed.subject,
                parent_shas=parsed.parent_shas,
                is_merge=parsed.is_merge,
                insertions=_sum_lines(parsed, "insertions"),
                deletions=_sum_lines(parsed, "deletions"),
                files_changed=len(parsed.changes),
            )
            for change in parsed.changes:
                commit.files.append(
                    CommitFile(
                        repository_id=repository.id,
                        path=change.path,
                        old_path=change.old_path,
                        change_type=change.change_type,
                        insertions=change.insertions,
                        deletions=change.deletions,
                        is_binary=change.is_binary,
                    )
                )
            db.add(commit)
            processed += 1
            if processed % _INSERT_BATCH == 0:
                db.flush()

        db.flush()

        repository.head_sha = head_sha
        repository.default_branch = default_branch
        repository.status = RepositoryStatus.READY
        repository.error_message = None
        repository.commit_count = processed
        repository.author_count = resolver.author_count
        repository.last_ingested_at = datetime.now(timezone.utc)
        db.commit()

        return IngestionStats(
            commit_count=processed,
            author_count=resolver.author_count,
            head_sha=head_sha,
            default_branch=default_branch,
        )
    except IngestionError as exc:
        db.rollback()
        repository.status = RepositoryStatus.ERROR
        repository.error_message = str(exc)[:2000]
        db.commit()
        raise
    except Exception as exc:
        db.rollback()
        repository.status = RepositoryStatus.ERROR
        repository.error_message = str(exc)[:2000]
        db.commit()
        raise IngestionError(f"ingestion failed: {exc}") from exc


def _sum_lines(parsed: ParsedCommit, attribute: str) -> int | None:
    if not parsed.changes:
        return None
    return sum(
        value for change in parsed.changes if (value := getattr(change, attribute)) is not None
    )
