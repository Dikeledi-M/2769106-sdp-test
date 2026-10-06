"""Importing repositories from ZIP archives and remote URLs.

Two sources are supported (mirroring ``RepositorySource``):

* ``upload`` - a ZIP archive of a working copy that includes the ``.git``
  directory. The archive is extracted (zip-slip safe) and the repository
  root inside it is located by looking for a ``.git`` entry.
* ``remote`` - a remote URL that is deeply cloned with ``git clone`` (full
  history - no shallow truncation).

Both imports run as background tasks: the HTTP request only stores the
archive / schedules the clone, and the repository row moves through
``pending -> ingesting -> ready`` (or ``error`` with a message) while the
dashboard polls it. The background functions open their own database
session because the request-scoped session is closed once the response has
been sent.

On re-import the ZIP / clone directories are replaced from scratch, while
canonical authors and identity merges stay intact (see ``git_ingest``).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import zipfile
from collections.abc import Callable
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.repository import Repository, RepositoryStatus
from app.services.git_ingest import IngestionError, ingest_repository

_CLONE_TIMEOUT_SECONDS = 600


def find_repository_root(directory: Path) -> Path | None:
    """Locate the repository root (a directory containing ``.git``).

    Archives usually wrap everything in a single top-level folder, so the
    search accepts a ``.git`` entry up to two levels deep. ``.git`` may be a
    directory or a file (gitlinks/worktrees); usability is decided later by
    git itself.
    """
    directory = Path(directory)
    if (directory / ".git").exists():
        return directory

    for candidate in sorted(directory.rglob(".git")):
        root = candidate.parent
        if "__MACOSX" in root.parts:
            continue
        if len(root.relative_to(directory).parts) <= 2:
            return root
    return None


def _safe_extract(archive_path: Path, target_dir: Path) -> None:
    """Extract a ZIP archive, refusing entries that escape ``target_dir``."""
    target_dir = Path(target_dir).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            member_target = (target_dir / member.filename).resolve()
            if not member_target.is_relative_to(target_dir):
                raise IngestionError(
                    f"archive entry escapes the extraction directory: {member.filename}"
                )
        archive.extractall(target_dir)


def extract_archive(archive_path: Path, target_dir: Path) -> Path:
    """Extract an uploaded archive and return the repository root inside it."""
    try:
        _safe_extract(archive_path, target_dir)
    except zipfile.BadZipFile as exc:
        raise IngestionError("the uploaded file is not a valid ZIP archive") from exc
    root = find_repository_root(target_dir)
    if root is None:
        raise IngestionError("no .git directory found in the archive")
    return root


def clone_repository(url: str, target_dir: Path) -> Path:
    """Deep-clone ``url`` (full history) into ``target_dir`` and return it."""
    target_dir = Path(target_dir)
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    # Fail fast instead of hanging on an interactive credential prompt.
    environment["GIT_TERMINAL_PROMPT"] = "0"
    try:
        process = subprocess.run(
            ["git", "clone", "--quiet", url, str(target_dir)],
            capture_output=True,
            env=environment,
            timeout=_CLONE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise IngestionError(
            f"git clone timed out after {_CLONE_TIMEOUT_SECONDS} seconds"
        ) from exc
    if process.returncode != 0:
        message = process.stderr.decode("utf-8", errors="replace").strip()
        raise IngestionError(message or f"git clone failed for {url}")
    return target_dir


def purge_repository_files(repository_id: int) -> None:
    """Best-effort removal of the stored archive / clone of a repository."""
    for base_dir in (settings.UPLOAD_DIR, settings.CLONE_DIR):
        shutil.rmtree(Path(base_dir) / str(repository_id), ignore_errors=True)


# ---------------------------------------------------------------------------
# Background tasks (one database session per run)
# ---------------------------------------------------------------------------


def ingest_uploaded_archive(
    repository_id: int, archive_path: Path, extract_dir: Path
) -> None:
    """Background task: extract an uploaded ZIP and ingest the repository."""
    _import_and_ingest(
        repository_id,
        prepare=lambda: extract_archive(archive_path, extract_dir),
        failure_prefix="could not import archive",
    )


def clone_and_ingest(repository_id: int, url: str, clone_dir: Path) -> None:
    """Background task: deep-clone a remote URL and ingest the repository."""
    _import_and_ingest(
        repository_id,
        prepare=lambda: clone_repository(url, clone_dir),
        failure_prefix="could not clone repository",
    )


def _import_and_ingest(
    repository_id: int, prepare: Callable[[], Path], failure_prefix: str
) -> None:
    db = SessionLocal()
    try:
        repository = db.get(Repository, repository_id)
        if repository is None:  # deleted while the task was queued
            return
        try:
            repo_path = prepare()
        except Exception as exc:  # noqa: BLE001 - surfaced on the repository row
            _mark_error(db, repository, f"{failure_prefix}: {exc}")
            return
        try:
            ingest_repository(db, repository, repo_path)
        except IngestionError:
            pass  # ingest_repository has already marked the repository as ERROR
    finally:
        db.close()


def _mark_error(db: Session, repository: Repository, message: str) -> None:
    repository.status = RepositoryStatus.ERROR
    repository.error_message = message[:2000]
    db.commit()
