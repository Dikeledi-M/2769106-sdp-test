"""Repository management endpoints: import, list, inspect, delete.

Repositories can be imported in the two forms required by the spec:

* ``POST /repositories/upload`` - multipart ZIP archive of a working copy
  that includes ``.git``;
* ``POST /repositories/clone`` - remote URL that is deeply cloned.

Both imports run in the background: the row starts as ``pending``, moves to
``ingesting`` and finally ``ready`` (or ``error`` with ``error_message``),
so the dashboard polls ``GET /repositories/{id}``. The auxiliary listings
(authors, commits, files) power the dashboard filters - authors scope the
commit set, commits enumerate it for manual selection, and files/directories
suggest paths for the path filter.
"""
from pathlib import Path
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_owned_repository
from app.core.config import settings
from app.db.session import get_db
from app.models.author import Author
from app.models.commit import Commit, CommitFile
from app.models.repository import Repository, RepositorySource
from app.schemas.repository import (
    AuthorRead,
    CommitRead,
    FileListResponse,
    RepositoryCloneRequest,
    RepositoryRead,
)
from app.services.repository_import import (
    clone_and_ingest,
    ingest_uploaded_archive,
    purge_repository_files,
)

router = APIRouter(prefix="/repositories", tags=["repositories"])

DbSession = Annotated[Session, Depends(get_db)]

_UPLOAD_CHUNK_BYTES = 1024 * 1024


def _repository_name_from_url(url: str) -> str:
    """Best-effort display name: the last URL segment without ``.git``."""
    cleaned = url.rstrip("/")
    segment = cleaned.rsplit("/", 1)[-1] if "/" in cleaned else cleaned
    if segment.endswith(".git"):
        segment = segment[:-4]
    return segment or "repository"


@router.get("", response_model=list[RepositoryRead])
def list_repositories(current_user: CurrentUser, db: DbSession) -> list[Repository]:
    """Every repository owned by the current user, newest first."""
    return list(
        db.execute(
            select(Repository)
            .where(Repository.owner_id == current_user.id)
            .order_by(Repository.created_at.desc(), Repository.id.desc())
        ).scalars()
    )


@router.post("/upload", response_model=RepositoryRead, status_code=status.HTTP_201_CREATED)
async def upload_repository(
    background_tasks: BackgroundTasks,
    current_user: CurrentUser,
    db: DbSession,
    file: Annotated[
        UploadFile,
        File(description="ZIP archive of the working copy, including the .git directory"),
    ],
    name: Annotated[str | None, Form(description="Optional display name")] = None,
) -> Repository:
    """Accept a ZIP archive and ingest it in the background."""
    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only .zip archives are accepted",
        )

    repository = Repository(
        name=(name or Path(file.filename or "repository").stem or "repository")[:255],
        source_type=RepositorySource.UPLOAD,
        owner_id=current_user.id,
    )
    db.add(repository)
    db.commit()
    db.refresh(repository)

    upload_dir = Path(settings.UPLOAD_DIR) / str(repository.id)
    upload_dir.mkdir(parents=True, exist_ok=True)
    archive_path = upload_dir / "upload.zip"
    with archive_path.open("wb") as target:
        while chunk := await file.read(_UPLOAD_CHUNK_BYTES):
            target.write(chunk)

    background_tasks.add_task(
        ingest_uploaded_archive, repository.id, archive_path, upload_dir / "repo"
    )
    return repository


@router.post("/clone", response_model=RepositoryRead, status_code=status.HTTP_201_CREATED)
def clone_repository(
    payload: RepositoryCloneRequest,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser,
    db: DbSession,
) -> Repository:
    """Deep-clone a remote URL and ingest it in the background."""
    repository = Repository(
        name=(payload.name or _repository_name_from_url(payload.url))[:255],
        source_type=RepositorySource.REMOTE,
        source_url=payload.url,
        owner_id=current_user.id,
    )
    db.add(repository)
    db.commit()
    db.refresh(repository)

    clone_dir = Path(settings.CLONE_DIR) / str(repository.id) / "repo"
    background_tasks.add_task(clone_and_ingest, repository.id, payload.url, clone_dir)
    return repository


@router.get("/{repository_id}", response_model=RepositoryRead)
def get_repository(
    repository_id: int, current_user: CurrentUser, db: DbSession
) -> Repository:
    """Status/counters of one repository (polled while ingesting)."""
    return get_owned_repository(db, repository_id, current_user)


@router.delete("/{repository_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_repository(repository_id: int, current_user: CurrentUser, db: DbSession) -> None:
    """Delete a repository, its commits/authors, and its stored files."""
    repository = get_owned_repository(db, repository_id, current_user)
    db.delete(repository)
    db.commit()
    purge_repository_files(repository_id)


@router.get("/{repository_id}/authors", response_model=list[AuthorRead])
def list_repository_authors(
    repository_id: int, current_user: CurrentUser, db: DbSession
) -> list[Author]:
    """Canonical authors of a repository (options for the author filter)."""
    repository = get_owned_repository(db, repository_id, current_user)
    return list(
        db.execute(
            select(Author)
            .where(Author.repository_id == repository.id)
            .order_by(Author.display_name)
        ).scalars()
    )


@router.get("/{repository_id}/commits", response_model=list[CommitRead])
def list_repository_commits(
    repository_id: int,
    current_user: CurrentUser,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Commit]:
    """Commits newest first (used to pick a manual commit list)."""
    repository = get_owned_repository(db, repository_id, current_user)
    statement = (
        select(Commit)
        .where(Commit.repository_id == repository.id)
        .order_by(Commit.committed_at.desc(), Commit.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(db.execute(statement).scalars())


@router.get("/{repository_id}/files", response_model=FileListResponse)
def list_repository_files(
    repository_id: int, current_user: CurrentUser, db: DbSession
) -> FileListResponse:
    """Distinct non-binary paths ever changed (options for the path filter)."""
    repository = get_owned_repository(db, repository_id, current_user)
    paths = db.execute(
        select(CommitFile.path)
        .where(
            CommitFile.repository_id == repository.id,
            CommitFile.is_binary.is_(False),
        )
        .distinct()
        .order_by(CommitFile.path)
    ).scalars()
    return FileListResponse(files=[str(path) for path in paths])
