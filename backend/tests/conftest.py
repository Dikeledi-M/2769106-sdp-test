"""Shared test fixtures: an in-memory SQLite database and a TestClient."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.repository import Repository, RepositorySource
from app.models.user import User
from app.services.git_ingest import ingest_repository
from tests.git_history import ScriptedRepo, build_scripted_repo


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session_local = sessionmaker(
        bind=engine, autocommit=False, autoflush=False, expire_on_commit=False
    )
    Base.metadata.create_all(bind=engine)

    session = testing_session_local()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def client(db_session: Session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def registered_user(client: TestClient) -> dict:
    """A registered user plus a valid token pair."""
    user_payload = {
        "email": "dev@example.com",
        "username": "dev",
        "password": "s3cret-pass",
        "full_name": "Dev Example",
    }
    response = client.post("/api/v1/auth/register", json=user_payload)
    assert response.status_code == 201, response.text

    login = client.post(
        "/api/v1/auth/login",
        json={"email": user_payload["email"], "password": user_payload["password"]},
    )
    assert login.status_code == 200, login.text
    return {"user": response.json(), "tokens": login.json()}


@pytest.fixture()
def owner(db_session: Session) -> User:
    """A user owning service-level repositories (no auth flow needed)."""
    user = User(email="owner@example.com", username="owner", hashed_password="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture()
def repository(db_session: Session, owner: User) -> Repository:
    """An empty, not yet ingested repository owned by ``owner``."""
    repository = Repository(
        name="scripted", source_type=RepositorySource.UPLOAD, owner_id=owner.id
    )
    db_session.add(repository)
    db_session.commit()
    db_session.refresh(repository)
    return repository


@pytest.fixture()
def scripted_repo(tmp_path: Path) -> ScriptedRepo:
    """The deterministic history from ``tests/git_history.py`` on disk."""
    return build_scripted_repo(tmp_path / "scripted-repo")


@pytest.fixture()
def ingested_repository(
    db_session: Session, repository: Repository, scripted_repo: ScriptedRepo
) -> Repository:
    """The scripted history ingested into ``repository``."""
    ingest_repository(db_session, repository, scripted_repo.path)
    return repository


@pytest.fixture()
def api_repository(
    db_session: Session, client: TestClient, registered_user: dict, scripted_repo: ScriptedRepo
) -> Repository:
    """An ingested repository owned by the registered API user."""
    repository = Repository(
        name="scripted",
        source_type=RepositorySource.UPLOAD,
        owner_id=registered_user["user"]["id"],
    )
    db_session.add(repository)
    db_session.commit()
    db_session.refresh(repository)
    ingest_repository(db_session, repository, scripted_repo.path)
    return repository
