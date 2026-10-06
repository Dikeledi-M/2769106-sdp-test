"""Shared test fixtures: an in-memory SQLite database and a TestClient."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.main import app


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
