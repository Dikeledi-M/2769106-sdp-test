"""End-to-end tests for the authentication flow."""
from fastapi.testclient import TestClient

API = "/api/v1"


def _register(client: TestClient, **overrides) -> dict:
    payload = {
        "email": "new@example.com",
        "username": "newuser",
        "password": "s3cret-pass",
        "full_name": "New User",
    }
    payload.update(overrides)
    response = client.post(f"{API}/auth/register", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _login(client: TestClient, email: str, password: str):
    return client.post(f"{API}/auth/login", json={"email": email, "password": password})


class TestRegister:
    def test_register_returns_user_without_password(self, client):
        body = _register(client)
        assert body["email"] == "new@example.com"
        assert body["username"] == "newuser"
        assert "password" not in body
        assert "hashed_password" not in body

    def test_register_normalizes_email_case(self, client):
        body = _register(client, email="Mixed.Case@Example.com")
        assert body["email"] == "mixed.case@example.com"

    def test_duplicate_email_rejected(self, client):
        _register(client)
        response = client.post(
            f"{API}/auth/register",
            json={
                "email": "new@example.com",
                "username": "different",
                "password": "s3cret-pass",
            },
        )
        assert response.status_code == 409
        assert response.json()["detail"] == "Email already registered"

    def test_duplicate_username_rejected(self, client):
        _register(client)
        response = client.post(
            f"{API}/auth/register",
            json={
                "email": "other@example.com",
                "username": "newuser",
                "password": "s3cret-pass",
            },
        )
        assert response.status_code == 409
        assert response.json()["detail"] == "Username already taken"

    def test_short_password_rejected(self, client):
        response = client.post(
            f"{API}/auth/register",
            json={"email": "a@example.com", "username": "abc", "password": "short"},
        )
        assert response.status_code == 422

    def test_password_over_bcrypt_limit_rejected(self, client):
        response = client.post(
            f"{API}/auth/register",
            json={"email": "a@example.com", "username": "abc", "password": "x" * 73},
        )
        assert response.status_code == 422


class TestLogin:
    def test_login_returns_token_pair(self, client, registered_user):
        tokens = registered_user["tokens"]
        assert tokens["access_token"]
        assert tokens["refresh_token"]
        assert tokens["token_type"] == "bearer"

    def test_login_with_wrong_password(self, client, registered_user):
        response = _login(client, "dev@example.com", "wrong-password")
        assert response.status_code == 401
        assert response.json()["detail"] == "Incorrect email or password"

    def test_login_with_unknown_email(self, client):
        response = _login(client, "ghost@example.com", "whatever123")
        assert response.status_code == 401


class TestMe:
    def test_me_returns_current_user(self, client, registered_user):
        response = client.get(
            f"{API}/auth/me",
            headers={"Authorization": f"Bearer {registered_user['tokens']['access_token']}"},
        )
        assert response.status_code == 200
        assert response.json()["email"] == "dev@example.com"

    def test_me_requires_token(self, client):
        assert client.get(f"{API}/auth/me").status_code == 401

    def test_me_rejects_garbage_token(self, client):
        response = client.get(
            f"{API}/auth/me", headers={"Authorization": "Bearer not-a-jwt"}
        )
        assert response.status_code == 401

    def test_me_rejects_refresh_token(self, client, registered_user):
        response = client.get(
            f"{API}/auth/me",
            headers={"Authorization": f"Bearer {registered_user['tokens']['refresh_token']}"},
        )
        assert response.status_code == 401


class TestRefreshRotation:
    def test_refresh_issues_new_pair(self, client, registered_user):
        old_refresh = registered_user["tokens"]["refresh_token"]
        response = client.post(f"{API}/auth/refresh", json={"refresh_token": old_refresh})
        assert response.status_code == 200
        new_tokens = response.json()
        assert new_tokens["refresh_token"] != old_refresh

        # The new access token works.
        me = client.get(
            f"{API}/auth/me",
            headers={"Authorization": f"Bearer {new_tokens['access_token']}"},
        )
        assert me.status_code == 200

    def test_replayed_refresh_token_revokes_session(self, client, registered_user):
        old_refresh = registered_user["tokens"]["refresh_token"]
        first = client.post(f"{API}/auth/refresh", json={"refresh_token": old_refresh})
        assert first.status_code == 200
        rotated_refresh = first.json()["refresh_token"]

        # Replaying the old token fails ...
        replay = client.post(f"{API}/auth/refresh", json={"refresh_token": old_refresh})
        assert replay.status_code == 401

        # ... and, as a precaution, invalidates the rotated token too.
        after = client.post(f"{API}/auth/refresh", json={"refresh_token": rotated_refresh})
        assert after.status_code == 401

    def test_refresh_rejects_access_token(self, client, registered_user):
        response = client.post(
            f"{API}/auth/refresh",
            json={"refresh_token": registered_user["tokens"]["access_token"]},
        )
        assert response.status_code == 401


class TestLogout:
    def test_logout_revokes_refresh_token(self, client, registered_user):
        refresh_token = registered_user["tokens"]["refresh_token"]
        response = client.post(f"{API}/auth/logout", json={"refresh_token": refresh_token})
        assert response.status_code == 204

        reuse = client.post(f"{API}/auth/refresh", json={"refresh_token": refresh_token})
        assert reuse.status_code == 401

    def test_logout_is_idempotent_and_tolerates_garbage(self, client, registered_user):
        refresh_token = registered_user["tokens"]["refresh_token"]
        assert client.post(f"{API}/auth/logout", json={"refresh_token": refresh_token}).status_code == 204
        assert client.post(f"{API}/auth/logout", json={"refresh_token": refresh_token}).status_code == 204
        assert client.post(f"{API}/auth/logout", json={"refresh_token": "garbage"}).status_code == 204


class TestHealth:
    def test_health(self, client):
        response = client.get(f"{API}/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
