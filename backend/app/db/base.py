"""Import hub for Alembic and application startup.

Every model must be imported here so that ``Base.metadata`` contains the
complete schema for autogeneration and ``create_all``.
"""
from app.db.base_class import Base  # noqa: F401
from app.models.author import Author, AuthorIdentity, MailmapEntry  # noqa: F401
from app.models.commit import Commit, CommitFile  # noqa: F401
from app.models.repository import Repository  # noqa: F401
from app.models.user import RefreshToken, User  # noqa: F401

__all__ = ["Base"]
