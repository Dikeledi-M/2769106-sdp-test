"""Pydantic schemas for the repository management endpoints.

Datetimes arrive from SQLite as naive UTC values, so serializers re-attach
the UTC timezone; the frontend can then render them in local time reliably.
"""
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.repository import RepositorySource, RepositoryStatus


def _assume_utc(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class RepositoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    source_type: RepositorySource
    source_url: str | None
    default_branch: str | None
    head_sha: str | None
    status: RepositoryStatus
    error_message: str | None
    commit_count: int | None
    author_count: int | None
    created_at: datetime
    updated_at: datetime
    last_ingested_at: datetime | None

    @field_validator("created_at", "updated_at", "last_ingested_at")
    @classmethod
    def attach_utc(cls, value: datetime | None) -> datetime | None:
        return _assume_utc(value)


class RepositoryCloneRequest(BaseModel):
    url: str = Field(
        min_length=1, max_length=2048, description="Remote repository URL to clone"
    )
    name: str | None = Field(
        default=None, min_length=1, max_length=255, description="Optional display name"
    )

    @field_validator("url", "name")
    @classmethod
    def strip_whitespace(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()


class AuthorRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    display_name: str
    primary_email: str | None
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def attach_utc(cls, value: datetime) -> datetime:
        return _assume_utc(value)  # type: ignore[return-value]


class CommitRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sha: str
    message: str | None
    author_id: int | None
    author_name: str | None
    committed_at: datetime | None
    is_merge: bool
    insertions: int | None
    deletions: int | None
    files_changed: int | None

    @field_validator("committed_at")
    @classmethod
    def attach_utc(cls, value: datetime | None) -> datetime | None:
        return _assume_utc(value)


class FileListResponse(BaseModel):
    files: list[str] = Field(default_factory=list)
