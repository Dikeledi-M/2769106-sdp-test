"""Commits and their per-file changes - the raw material for all metrics.

Directories are derived from file paths at query time (``path`` prefixes),
so no separate directory table is needed. Aggregating ``CommitFile`` rows
per author / path / commit range is what produces the dashboard metrics.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base

if TYPE_CHECKING:
    from app.models.author import Author
    from app.models.repository import Repository


class Commit(Base):
    """A single git commit with raw author metadata and diff statistics.

    ``author_id`` is the resolved canonical developer; ``author_name`` /
    ``author_email`` always keep the raw values so nothing is lost before
    identities are merged. Metrics queries can therefore run both on raw
    identities and on merged authors.
    """

    __tablename__ = "commits"
    __table_args__ = (
        UniqueConstraint("repository_id", "sha", name="uq_commit_repo_sha"),
        Index("ix_commits_repo_authored_at", "repository_id", "authored_at"),
        Index("ix_commits_repo_committed_at", "repository_id", "committed_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True, nullable=False
    )
    sha: Mapped[str] = mapped_column(String(64), index=True, nullable=False)

    author_id: Mapped[int | None] = mapped_column(
        ForeignKey("authors.id", ondelete="SET NULL"), index=True
    )
    author_identity_id: Mapped[int | None] = mapped_column(
        ForeignKey("author_identities.id", ondelete="SET NULL")
    )

    author_name: Mapped[str | None] = mapped_column(String(255))
    author_email: Mapped[str | None] = mapped_column(String(320))
    authored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    committer_name: Mapped[str | None] = mapped_column(String(255))
    committer_email: Mapped[str | None] = mapped_column(String(320))
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    message: Mapped[str | None] = mapped_column(Text)
    parent_shas: Mapped[list[str] | None] = mapped_column(JSON)
    is_merge: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    insertions: Mapped[int | None] = mapped_column(Integer)
    deletions: Mapped[int | None] = mapped_column(Integer)
    files_changed: Mapped[int | None] = mapped_column(Integer)

    repository: Mapped[Repository] = relationship(back_populates="commits")
    author: Mapped[Author | None] = relationship(back_populates="commits")
    files: Mapped[list[CommitFile]] = relationship(
        back_populates="commit", cascade="all, delete-orphan"
    )


class CommitFile(Base):
    """A per-file change made by a commit (base for file / directory metrics)."""

    __tablename__ = "commit_files"
    __table_args__ = (Index("ix_commit_files_repo_path", "repository_id", "path"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    commit_id: Mapped[int] = mapped_column(
        ForeignKey("commits.id", ondelete="CASCADE"), index=True, nullable=False
    )
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True, nullable=False
    )
    path: Mapped[str] = mapped_column(String(1024), nullable=False)
    old_path: Mapped[str | None] = mapped_column(String(1024))
    change_type: Mapped[str] = mapped_column(String(1), nullable=False)  # A/M/D/R/C/T
    insertions: Mapped[int | None] = mapped_column(Integer)
    deletions: Mapped[int | None] = mapped_column(Integer)
    is_binary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    commit: Mapped[Commit] = relationship(back_populates="files")
