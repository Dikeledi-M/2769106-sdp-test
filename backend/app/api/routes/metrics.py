"""File and directory metrics endpoints.

The commit set ``H`` defaults to every non-merge commit reachable from HEAD
(i.e. everything ingested) and can be narrowed with:

* ``from`` / ``to`` - half-open committer-date range (``from`` inclusive,
  ``to`` exclusive), ISO 8601 timestamps;
* ``commit`` (repeatable) - explicit commit SHAs, intersected with the set;
* ``author`` (repeatable) - canonical author ids to keep;
* ``reference`` - keep only commits reachable from this SHA.

``path`` restricts the reported objects to a file or directory and its
contents. Repositories are only visible to their owner (404 otherwise).
"""
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser
from app.db.session import get_db
from app.models.repository import Repository
from app.models.user import User
from app.schemas.metrics import DirectoryMetricsResponse, FileMetricsResponse
from app.services.metrics import (
    MetricsError,
    MetricsFilter,
    directory_metrics,
    file_metrics,
)

router = APIRouter(prefix="/repositories", tags=["metrics"])

DbSession = Annotated[Session, Depends(get_db)]


def commit_set_filter(
    from_ts: Annotated[
        datetime | None,
        Query(alias="from", description="Commits with committer date >= this timestamp"),
    ] = None,
    to_ts: Annotated[
        datetime | None,
        Query(alias="to", description="Commits with committer date < this timestamp"),
    ] = None,
    commit: Annotated[
        list[str] | None, Query(description="Explicit commit SHAs (repeatable)")
    ] = None,
    author: Annotated[
        list[int] | None, Query(description="Canonical author ids (repeatable)")
    ] = None,
    reference: Annotated[
        str | None, Query(description="Keep only commits reachable from this SHA")
    ] = None,
    path: Annotated[
        str | None, Query(description="Restrict the reported objects to this path")
    ] = None,
) -> MetricsFilter:
    return MetricsFilter(
        from_ts=from_ts,
        to_ts=to_ts,
        commit_shas=tuple(commit or ()),
        author_ids=tuple(author or ()),
        reference_sha=reference,
        path=path,
    )


CommitSetFilter = Annotated[MetricsFilter, Depends(commit_set_filter)]


def _get_owned_repository(db: Session, repository_id: int, user: User) -> Repository:
    repository = db.get(Repository, repository_id)
    if repository is None or repository.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")
    return repository


def _bad_reference(exc: MetricsError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("/{repository_id}/metrics/files", response_model=FileMetricsResponse)
def get_file_metrics(
    repository_id: int,
    filters: CommitSetFilter,
    current_user: CurrentUser,
    db: DbSession,
) -> FileMetricsResponse:
    repository = _get_owned_repository(db, repository_id, current_user)
    try:
        result = file_metrics(db, repository, filters)
    except MetricsError as exc:
        raise _bad_reference(exc) from exc
    return FileMetricsResponse(commit_count=result.commit_count, files=result.objects)


@router.get("/{repository_id}/metrics/directories", response_model=DirectoryMetricsResponse)
def get_directory_metrics(
    repository_id: int,
    filters: CommitSetFilter,
    current_user: CurrentUser,
    db: DbSession,
) -> DirectoryMetricsResponse:
    repository = _get_owned_repository(db, repository_id, current_user)
    try:
        result = directory_metrics(db, repository, filters)
    except MetricsError as exc:
        raise _bad_reference(exc) from exc
    return DirectoryMetricsResponse(
        commit_count=result.commit_count, directories=result.objects
    )
