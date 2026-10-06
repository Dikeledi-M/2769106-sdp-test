"""Tests for repository management: ZIP upload, remote clone, and listings.

The upload flow zips the deterministic scripted history (including its
``.git`` directory) and posts it as multipart data; the clone flow uses the
scripted repository's path as a local "remote" (``git clone`` supports file
paths). Background tasks run synchronously under the TestClient, so each
import is fully ingested by the time the response is returned.
"""
from __future__ import annotations

import os
import zipfile
from pathlib import Path

import pytest

from app.models.repository import Repository
from app.services.repository_import import (
    IngestionError,
    extract_archive,
    find_repository_root,
)
from tests.git_history import ScriptedRepo

API = "/api/v1"

EXPECTED_FILES = ["a.txt", "feature.txt", "readme.txt", "sub/b.txt", "sub/c.txt", "sub/d.txt"]


def _zip_directory(source: Path, archive_path: Path, prefix: str = "") -> Path:
    """Zip a directory tree (including dotfolders such as ``.git``)."""
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for root, _dirs, files in os.walk(source):
            root_path = Path(root)
            for name in files:
                full = root_path / name
                archive.write(full, prefix + full.relative_to(source).as_posix())
    return archive_path


def _headers(registered_user: dict) -> dict:
    return {"Authorization": f"Bearer {registered_user['tokens']['access_token']}"}


def _upload(client, registered_user: dict, zip_path: Path, name: str | None = None):
    data = {"name": name} if name else None
    with zip_path.open("rb") as handle:
        return client.post(
            f"{API}/repositories/upload",
            files={"file": (zip_path.name, handle, "application/zip")},
            data=data,
            headers=_headers(registered_user),
        )


def _clone(client, registered_user: dict, url: str, name: str | None = None):
    payload: dict = {"url": url}
    if name:
        payload["name"] = name
    return client.post(
        f"{API}/repositories/clone", json=payload, headers=_headers(registered_user)
    )


def _detail(client, registered_user: dict, repository_id: int) -> dict:
    response = client.get(
        f"{API}/repositories/{repository_id}", headers=_headers(registered_user)
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture()
def uploaded_api_repository(client, registered_user, scripted_repo, storage_dirs) -> dict:
    """The scripted history uploaded through the API and fully ingested."""
    archive = _zip_directory(scripted_repo.path, storage_dirs[0] / "scripted.zip")
    response = _upload(client, registered_user, archive)
    assert response.status_code == 201, response.text
    return response.json()


class TestUpload:
    def test_upload_zip_ingests_repository(
        self, client, registered_user, scripted_repo, storage_dirs
    ):
        archive = _zip_directory(scripted_repo.path, storage_dirs[0] / "scripted.zip")
        response = _upload(client, registered_user, archive)
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["name"] == "scripted"
        assert body["source_type"] == "upload"
        assert body["status"] == "pending"  # response is sent before the import runs

        payload = _detail(client, registered_user, body["id"])
        assert payload["status"] == "ready"
        assert payload["error_message"] is None
        assert payload["commit_count"] == 8  # c1..c6, feature, merge
        assert payload["author_count"] == 2
        assert payload["head_sha"] == scripted_repo.shas["merge"]
        assert payload["default_branch"] == "main"
        assert payload["last_ingested_at"] is not None

        # metrics work end-to-end on the freshly imported repository
        metrics = client.get(
            f"{API}/repositories/{body['id']}/metrics/repository",
            headers=_headers(registered_user),
        )
        assert metrics.status_code == 200, metrics.text
        root = metrics.json()["repository"]
        assert (root["churn"], root["modifications"]) == (37, 6)

    def test_upload_custom_name(self, client, registered_user, scripted_repo, storage_dirs):
        archive = _zip_directory(scripted_repo.path, storage_dirs[0] / "scripted.zip")
        response = _upload(client, registered_user, archive, name="My Project")
        assert response.status_code == 201, response.text
        assert response.json()["name"] == "My Project"

    def test_upload_archive_wrapped_in_folders(
        self, client, registered_user, scripted_repo, storage_dirs
    ):
        archive = _zip_directory(
            scripted_repo.path,
            storage_dirs[0] / "wrapped.zip",
            prefix="nested/scripted-repo/",
        )
        response = _upload(client, registered_user, archive)
        assert response.status_code == 201, response.text
        assert _detail(client, registered_user, response.json()["id"])["status"] == "ready"

    def test_upload_rejects_non_zip(self, client, registered_user, storage_dirs):
        response = client.post(
            f"{API}/repositories/upload",
            files={"file": ("notes.txt", b"hello", "text/plain")},
            headers=_headers(registered_user),
        )
        assert response.status_code == 400

    def test_upload_broken_archive_marks_error(self, client, registered_user, storage_dirs):
        response = client.post(
            f"{API}/repositories/upload",
            files={"file": ("broken.zip", b"definitely not a zip", "application/zip")},
            headers=_headers(registered_user),
        )
        assert response.status_code == 201, response.text
        payload = _detail(client, registered_user, response.json()["id"])
        assert payload["status"] == "error"
        assert "not a valid ZIP archive" in payload["error_message"]

    def test_upload_without_git_marks_error(
        self, client, registered_user, storage_dirs, tmp_path
    ):
        plain = tmp_path / "plain"
        plain.mkdir()
        (plain / "file.txt").write_text("no git here\n", encoding="utf-8")
        archive = _zip_directory(plain, storage_dirs[0] / "plain.zip")

        response = _upload(client, registered_user, archive)
        assert response.status_code == 201, response.text
        payload = _detail(client, registered_user, response.json()["id"])
        assert payload["status"] == "error"
        assert "no .git directory found" in payload["error_message"]


class TestClone:
    def test_clone_local_path_ingests(
        self, client, registered_user, scripted_repo: ScriptedRepo, storage_dirs
    ):
        response = _clone(client, registered_user, str(scripted_repo.path))
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["source_type"] == "remote"
        assert body["source_url"] == str(scripted_repo.path)
        assert body["name"] == "scripted-repo"  # derived from the URL

        payload = _detail(client, registered_user, body["id"])
        assert payload["status"] == "ready"
        assert payload["commit_count"] == 8
        assert payload["head_sha"] == scripted_repo.shas["merge"]

    def test_clone_failure_marks_error(self, client, registered_user, storage_dirs, tmp_path):
        response = _clone(client, registered_user, str(tmp_path / "missing-repo"))
        assert response.status_code == 201, response.text
        payload = _detail(client, registered_user, response.json()["id"])
        assert payload["status"] == "error"
        assert "could not clone repository" in payload["error_message"]

    def test_clone_requires_url(self, client, registered_user):
        response = client.post(
            f"{API}/repositories/clone", json={}, headers=_headers(registered_user)
        )
        assert response.status_code == 422


class TestListings:
    def test_list_repositories(self, client, registered_user, uploaded_api_repository):
        response = client.get(f"{API}/repositories", headers=_headers(registered_user))
        assert response.status_code == 200, response.text
        body = response.json()
        assert [item["id"] for item in body] == [uploaded_api_repository["id"]]

    def test_authors_listing(self, client, registered_user, uploaded_api_repository):
        response = client.get(
            f"{API}/repositories/{uploaded_api_repository['id']}/authors",
            headers=_headers(registered_user),
        )
        assert response.status_code == 200, response.text
        authors = response.json()
        assert [author["display_name"] for author in authors] == ["Alice", "Bob"]
        assert sorted(author["primary_email"] for author in authors) == [
            "alice@example.com",
            "bob@example.com",
        ]

    def test_commits_listing(self, client, registered_user, uploaded_api_repository, scripted_repo):
        url = f"{API}/repositories/{uploaded_api_repository['id']}/commits"
        response = client.get(url, headers=_headers(registered_user))
        assert response.status_code == 200, response.text
        commits = response.json()
        assert len(commits) == 8
        assert commits[0]["sha"] == scripted_repo.shas["merge"]  # newest first
        assert commits[0]["is_merge"] is True
        assert commits[0]["committed_at"].endswith("Z")  # UTC re-attached

        limited = client.get(
            url, params={"limit": 3, "offset": 1}, headers=_headers(registered_user)
        )
        assert limited.status_code == 200
        assert len(limited.json()) == 3

    def test_files_listing_excludes_binary(
        self, client, registered_user, uploaded_api_repository
    ):
        response = client.get(
            f"{API}/repositories/{uploaded_api_repository['id']}/files",
            headers=_headers(registered_user),
        )
        assert response.status_code == 200, response.text
        assert response.json()["files"] == EXPECTED_FILES
        assert "logo.bin" not in response.json()["files"]

    def test_delete_repository(
        self, client, db_session, registered_user, uploaded_api_repository
    ):
        repository_id = uploaded_api_repository["id"]
        response = client.delete(
            f"{API}/repositories/{repository_id}", headers=_headers(registered_user)
        )
        assert response.status_code == 204
        assert (
            client.get(
                f"{API}/repositories/{repository_id}", headers=_headers(registered_user)
            ).status_code
            == 404
        )
        assert db_session.get(Repository, repository_id) is None

    def test_repositories_are_isolated_per_owner(self, client, registered_user, uploaded_api_repository):
        other = {
            "email": "other@example.com",
            "username": "other",
            "password": "s3cret-pass",
            "full_name": "Other User",
        }
        assert client.post(f"{API}/auth/register", json=other).status_code == 201
        login = client.post(
            f"{API}/auth/login",
            json={"email": other["email"], "password": other["password"]},
        )
        assert login.status_code == 200, login.text
        other_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        assert client.get(f"{API}/repositories", headers=other_headers).json() == []
        repository_id = uploaded_api_repository["id"]
        assert (
            client.get(f"{API}/repositories/{repository_id}", headers=other_headers).status_code
            == 404
        )
        assert (
            client.delete(
                f"{API}/repositories/{repository_id}", headers=other_headers
            ).status_code
            == 404
        )

    def test_endpoints_require_authentication(self, client):
        assert client.get(f"{API}/repositories").status_code == 401
        assert client.get(f"{API}/repositories/1/authors").status_code == 401
        assert client.get(f"{API}/repositories/1/commits").status_code == 401
        assert client.get(f"{API}/repositories/1/files").status_code == 401


class TestImportHelpers:
    def test_find_repository_root_finds_top_level(self, scripted_repo):
        assert find_repository_root(scripted_repo.path) == scripted_repo.path

    def test_find_repository_root_returns_none(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        assert find_repository_root(empty) is None

    def test_zip_slip_is_rejected(self, tmp_path):
        archive = tmp_path / "evil.zip"
        with zipfile.ZipFile(archive, "w") as evil:
            evil.writestr("../evil.txt", "escaped!")
        with pytest.raises(IngestionError, match="escapes"):
            extract_archive(archive, tmp_path / "extracted")
        assert not (tmp_path / "evil.txt").exists()
