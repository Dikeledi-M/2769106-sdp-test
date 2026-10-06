"""Canonical authors, their raw git identities, and .mailmap entries.

Git history often contains the same person under several name/email pairs.
The RAT solves this by keeping two levels:

* ``Author`` - the canonical developer shown in metrics (scoped per
  repository, so merging decisions never leak between projects).
* ``AuthorIdentity`` - a raw (name, email) pair exactly as it appears in
  commits. Merging authors re-points identities to the canonical author;
  commits follow through their ``author_id`` foreign key.

Identities can be merged automatically (same email), through a ``.mailmap``
file, or manually by a user in the UI. Because identities are upserted by
email on re-ingestion, manual merges survive repository re-imports.
"""
from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base

if TYPE_CHECKING:
    from app.models.commit import Commit
    from app.models.repository import Repository


class IdentitySource(str, enum.Enum):
    """How the mapping 'raw identity -> canonical author' was established."""

    AUTO = "auto"  # grouped automatically by email
    MAILMAP = "mailmap"  # resolved through a .mailmap file
    MANUAL = "manual"  # merged by a user in the dashboard


class Author(Base):
    """Canonical developer entity that all metrics are grouped by."""

    __tablename__ = "authors"

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True, nullable=False
    )
    display_name: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    primary_email: Mapped[str | None] = mapped_column(String(320))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    repository: Mapped[Repository] = relationship(back_populates="authors")
    identities: Mapped[list[AuthorIdentity]] = relationship(
        back_populates="author", cascade="all, delete-orphan"
    )
    commits: Mapped[list[Commit]] = relationship(back_populates="author")


class AuthorIdentity(Base):
    """A raw (name, email) pair seen in git commits."""

    __tablename__ = "author_identities"
    __table_args__ = (
        UniqueConstraint("repository_id", "email", "name", name="uq_author_identity_repo_email_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    author_id: Mapped[int] = mapped_column(
        ForeignKey("authors.id", ondelete="CASCADE"), index=True, nullable=False
    )
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str | None] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(320), index=True, nullable=False)
    source: Mapped[IdentitySource] = mapped_column(
        Enum(
            IdentitySource,
            native_enum=False,
            length=32,
            values_callable=lambda cls: [member.value for member in cls],
        ),
        default=IdentitySource.AUTO,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    author: Mapped[Author] = relationship(back_populates="identities")


class MailmapEntry(Base):
    """A ``.mailmap`` line translated into a raw -> canonical mapping.

    A mailmap line such as::

        Proper Name <proper@example.com> Old Name <old@example.com>

    produces one row with the canonical identity on the left and the raw
    identity that it should replace on the right.
    """

    __tablename__ = "mailmap_entries"
    __table_args__ = (
        UniqueConstraint("repository_id", "source_name", "source_email", name="uq_mailmap_entry"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True, nullable=False
    )
    canonical_name: Mapped[str | None] = mapped_column(String(255))
    canonical_email: Mapped[str] = mapped_column(String(320), nullable=False)
    source_name: Mapped[str | None] = mapped_column(String(255))
    source_email: Mapped[str | None] = mapped_column(String(320))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
