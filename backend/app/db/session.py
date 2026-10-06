"""Database engine / session management and the FastAPI ``get_db`` dependency."""
from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

connect_args = {"check_same_thread": False} if settings.is_sqlite else {}

engine = create_engine(
    settings.DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


if settings.is_sqlite:

    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        """SQLite does not enforce foreign keys unless explicitly asked to."""
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a database session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
