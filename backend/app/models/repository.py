"""Tracked repositories and their ingestion state."""
from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base

if TYPE_CHECKING:
    from app.models.author import Author
    from app.models.commit import Commit
    from app.models.user import User


class RepositorySource(str, enum.Enum):
    """How the repository content was obtained."""

    UPLOAD = "upload"  # ZIP archive of the working tree including .git
    REMOTE = "remote"  # remote URL that is deeply cloned


class RepositoryStatus(str, enum.Enum):
    PENDING = "pending"
    INGESTING = "ingesting"
    READY = "ready"
    ERROR = "error"


def _enum_column(enum_cls: type[enum.Enum], length: int) -> Enum:
    """String-backed enum that stores member *values* (portable to Postgres)."""
    return Enum(
        enum_cls,
        native_enum=False,
        length=length,
        values_callable=lambda cls: [member.value for member in cls],
    )


class Repository(Base):
    __tablename__ = "repositories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    source_type: Mapped[RepositorySource] = mapped_column(
        _enum_column(RepositorySource, 32), nullable=False
    )
    source_url: Mapped[str | None] = mapped_column(String(2048))
    default_branch: Mapped[str | None] = mapped_column(String(255))
    head_sha: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[RepositoryStatus] = mapped_column(
        _enum_column(RepositoryStatus, 32), default=RepositoryStatus.PENDING, nullable=False
    )
    error_message: Mapped[str | None] = mapped_column(Text)

    # Cached aggregates, refreshed after every ingestion run.
    commit_count: Mapped[int | None] = mapped_column(Integer)
    author_count: Mapped[int | None] = mapped_column(Integer)

    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    last_ingested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    owner: Mapped[User] = relationship(back_populates="repositories")
    authors: Mapped[list[Author]] = relationship(
        back_populates="repository", cascade="all, delete-orphan"
    )
    commits: Mapped[list[Commit]] = relationship(
        back_populates="repository", cascade="all, delete-orphan"
    )
