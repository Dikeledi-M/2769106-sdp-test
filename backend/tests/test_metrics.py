"""Tests for the file / directory metrics engine and the metrics API.

Expected values are hand-derived from the scripted history documented in
``tests/git_history.py``; commit set boundary cases follow the spec:

* ``H_t`` is inclusive of ``t``, ``H_i,j`` is inclusive of ``i`` and
  exclusive of ``j`` (committer dates);
* ``H-bar`` excludes merge commits;
* renames attribute their changes to the new path, deletions to the
  deleted path, and binary files are never measured.
"""
import pytest
from sqlalchemy import select

from app.models.author import Author
from app.models.commit import Commit, CommitFile
from app.models.repository import Repository
from app.services.metrics import (
    MetricsError,
    MetricsFilter,
    directory_metrics,
    file_metrics,
    resolve_commit_ids,
)
from tests.git_history import (
    C1_TIME,
    C3_TIME,
    ScriptedRepo,
    dt,
)

API = "/api/v1"

# Expected totals over the full commit set (7 non-merge commits).
FULL_FILE_TOTALS = {
    "a.txt": (13, 2),  # +10 in c1, +3/-2 in c2
    "feature.txt": (2, 0),
    "readme.txt": (4, 0),
    "sub/b.txt": (6, 0),  # +5 in c1, +1 in c2; renames add nothing
    "sub/c.txt": (0, 0),  # pure rename target in c3
    "sub/d.txt": (2, 8),  # +2/-2 in c4, -6 deletion in c5
}
FULL_DIRECTORY_TOTALS = {"": (27, 10), "sub": (8, 8)}


def _pairs(result) -> dict[str, tuple[int, int]]:
    """``path -> (added, removed)`` for a ``MetricsResult``."""
    return {obj.path: (obj.added_lines, obj.removed_lines) for obj in result.objects}


def _author(db_session, repository: Repository, email: str) -> Author:
    return (
        db_session.execute(
            select(Author).where(
                Author.repository_id == repository.id, Author.primary_email == email
            )
        )
        .scalars()
        .one()
    )


def _headers(registered_user: dict) -> dict:
    return {"Authorization": f"Bearer {registered_user['tokens']['access_token']}"}


class TestFileMetrics:
    def test_full_commit_set(self, db_session, ingested_repository):
        result = file_metrics(db_session, ingested_repository)
        assert result.commit_count == 7
        assert _pairs(result) == FULL_FILE_TOTALS

    def test_objects_sorted_by_path(self, db_session, ingested_repository):
        result = file_metrics(db_session, ingested_repository)
        assert [obj.path for obj in result.objects] == sorted(FULL_FILE_TOTALS)

    def test_growth_and_churn(self, db_session, ingested_repository):
        result = file_metrics(db_session, ingested_repository)
        by_path = {obj.path: obj for obj in result.objects}
        assert (by_path["a.txt"].growth, by_path["a.txt"].churn) == (11, 15)
        assert (by_path["sub/d.txt"].growth, by_path["sub/d.txt"].churn) == (-6, 10)
        for obj in result.objects:  # the invariants hold for every object
            assert obj.growth == obj.added_lines - obj.removed_lines
            assert obj.churn == obj.added_lines + obj.removed_lines

    def test_binary_files_never_measured(self, db_session, ingested_repository):
        result = file_metrics(db_session, ingested_repository)
        assert "logo.bin" not in {obj.path for obj in result.objects}

    def test_empty_commit_set(self, db_session, ingested_repository):
        filters = MetricsFilter(from_ts=dt("2026-01-02T00:00:00+00:00"))
        result = file_metrics(db_session, ingested_repository, filters)
        assert result.commit_count == 0
        assert result.objects == []


class TestCommitSetResolution:
    def test_time_range_half_open(self, db_session, ingested_repository):
        # H_i,j with i = 00:00 inclusive and j = 02:00 exclusive -> c1, c2 only
        filters = MetricsFilter(from_ts=dt(C1_TIME), to_ts=dt(C3_TIME))
        result = file_metrics(db_session, ingested_repository, filters)
        assert result.commit_count == 2
        assert _pairs(result) == {"a.txt": (13, 2), "sub/b.txt": (6, 0)}

    def test_from_timestamp_inclusive(self, db_session, ingested_repository):
        # H_t from 02:00 -> c3 (exactly at the boundary), c4, c5, c6, feature
        filters = MetricsFilter(from_ts=dt(C3_TIME))
        result = file_metrics(db_session, ingested_repository, filters)
        assert result.commit_count == 5
        assert _pairs(result) == {
            "feature.txt": (2, 0),
            "readme.txt": (4, 0),
            "sub/c.txt": (0, 0),
            "sub/d.txt": (2, 8),
        }

    def test_to_timestamp_exclusive(self, db_session, ingested_repository):
        filters = MetricsFilter(to_ts=dt(C1_TIME))  # c1 sits exactly on the boundary
        result = file_metrics(db_session, ingested_repository, filters)
        assert result.commit_count == 0
        assert result.objects == []

    def test_explicit_commit_list(self, db_session, ingested_repository, scripted_repo: ScriptedRepo):
        filters = MetricsFilter(
            commit_shas=(scripted_repo.shas["c3"], scripted_repo.shas["c5"])
        )
        result = file_metrics(db_session, ingested_repository, filters)
        assert result.commit_count == 2
        assert _pairs(result) == {"sub/c.txt": (0, 0), "sub/d.txt": (0, 6)}

    def test_merge_commit_never_in_the_set(self, db_session, ingested_repository, scripted_repo):
        filters = MetricsFilter(commit_shas=(scripted_repo.shas["merge"],))
        result = file_metrics(db_session, ingested_repository, filters)
        assert result.commit_count == 0

    def test_author_filter(self, db_session, ingested_repository):
        bob = _author(db_session, ingested_repository, "bob@example.com")
        result = file_metrics(db_session, ingested_repository, MetricsFilter(author_ids=(bob.id,)))
        assert result.commit_count == 2
        assert _pairs(result) == {"feature.txt": (2, 0), "readme.txt": (4, 0)}

    def test_author_filter_follows_merged_authors(self, db_session, ingested_repository):
        alice = _author(db_session, ingested_repository, "alice@example.com")
        result = file_metrics(
            db_session, ingested_repository, MetricsFilter(author_ids=(alice.id,))
        )
        assert result.commit_count == 5
        assert _pairs(result) == {
            "a.txt": (13, 2),
            "sub/b.txt": (6, 0),
            "sub/c.txt": (0, 0),
            "sub/d.txt": (2, 8),
        }

    def test_reference_limits_to_ancestry(self, db_session, ingested_repository, scripted_repo):
        filters = MetricsFilter(reference_sha=scripted_repo.shas["c2"])
        result = file_metrics(db_session, ingested_repository, filters)
        assert result.commit_count == 2  # c1 and c2
        assert _pairs(result) == {"a.txt": (13, 2), "sub/b.txt": (6, 0)}

    def test_reference_branch_tip(self, db_session, ingested_repository, scripted_repo):
        filters = MetricsFilter(reference_sha=scripted_repo.shas["feature"])
        result = file_metrics(db_session, ingested_repository, filters)
        assert result.commit_count == 3  # c1, c2 and the feature commit
        assert _pairs(result) == {"a.txt": (13, 2), "feature.txt": (2, 0), "sub/b.txt": (6, 0)}

    def test_reference_head_covers_everything_but_merges(
        self, db_session, ingested_repository, scripted_repo
    ):
        filters = MetricsFilter(reference_sha=scripted_repo.shas["merge"])
        result = file_metrics(db_session, ingested_repository, filters)
        assert result.commit_count == 7
        assert _pairs(result) == FULL_FILE_TOTALS

    def test_unknown_reference_raises(self, db_session, ingested_repository):
        with pytest.raises(MetricsError, match="not found"):
            resolve_commit_ids(
                db_session, ingested_repository, MetricsFilter(reference_sha="0" * 40)
            )


class TestDirectoryMetrics:
    def test_rollup_includes_root(self, db_session, ingested_repository):
        result = directory_metrics(db_session, ingested_repository)
        assert result.commit_count == 7
        assert _pairs(result) == FULL_DIRECTORY_TOTALS

    def test_root_comes_first(self, db_session, ingested_repository):
        result = directory_metrics(db_session, ingested_repository)
        assert [obj.path for obj in result.objects] == ["", "sub"]

    def test_rollup_over_multiple_levels(self, db_session, repository):
        commit = Commit(
            repository_id=repository.id,
            sha="f" * 40,
            is_merge=False,
            committed_at=dt(C1_TIME),
        )
        commit.files.append(
            CommitFile(
                repository_id=repository.id,
                path="a/b/c.txt",
                change_type="A",
                insertions=7,
                deletions=2,
                is_binary=False,
            )
        )
        db_session.add(commit)
        db_session.commit()

        result = directory_metrics(db_session, repository)
        assert _pairs(result) == {"": (7, 2), "a": (7, 2), "a/b": (7, 2)}


class TestPathFilter:
    def test_directory_scope(self, db_session, ingested_repository):
        filters = MetricsFilter(path="sub")
        files = file_metrics(db_session, ingested_repository, filters)
        assert _pairs(files) == {"sub/b.txt": (6, 0), "sub/c.txt": (0, 0), "sub/d.txt": (2, 8)}
        directories = directory_metrics(db_session, ingested_repository, filters)
        assert _pairs(directories) == {"sub": (8, 8)}  # ancestors above the scope are dropped

    def test_file_scope(self, db_session, ingested_repository):
        filters = MetricsFilter(path="sub/d.txt")
        files = file_metrics(db_session, ingested_repository, filters)
        assert _pairs(files) == {"sub/d.txt": (2, 8)}
        directories = directory_metrics(db_session, ingested_repository, filters)
        assert directories.objects == []

    def test_trailing_slash_normalized(self, db_session, ingested_repository):
        files = file_metrics(db_session, ingested_repository, MetricsFilter(path="sub/"))
        assert _pairs(files) == {"sub/b.txt": (6, 0), "sub/c.txt": (0, 0), "sub/d.txt": (2, 8)}

    def test_prefix_must_match_a_path_segment(self, db_session, ingested_repository):
        files = file_metrics(db_session, ingested_repository, MetricsFilter(path="su"))
        assert files.objects == []


class TestMetricsApi:
    def test_file_metrics_endpoint(self, client, registered_user, api_repository):
        response = client.get(
            f"{API}/repositories/{api_repository.id}/metrics/files",
            headers=_headers(registered_user),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["commit_count"] == 7
        assert {
            item["path"]: (item["added_lines"], item["removed_lines"])
            for item in body["files"]
        } == FULL_FILE_TOTALS
        a_txt = next(item for item in body["files"] if item["path"] == "a.txt")
        assert (a_txt["growth"], a_txt["churn"]) == (11, 15)

    def test_directory_metrics_endpoint(self, client, registered_user, api_repository):
        response = client.get(
            f"{API}/repositories/{api_repository.id}/metrics/directories",
            headers=_headers(registered_user),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["commit_count"] == 7
        assert [item["path"] for item in body["directories"]] == ["", "sub"]
        sub = body["directories"][1]
        assert (sub["added_lines"], sub["removed_lines"]) == (8, 8)
        assert (sub["growth"], sub["churn"]) == (0, 16)

    def test_time_range_params(self, client, registered_user, api_repository):
        response = client.get(
            f"{API}/repositories/{api_repository.id}/metrics/files",
            params={"from": "2026-01-01T00:00:00Z", "to": C3_TIME},
            headers=_headers(registered_user),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["commit_count"] == 2
        assert {item["path"] for item in body["files"]} == {"a.txt", "sub/b.txt"}

    def test_author_param(self, client, db_session, registered_user, api_repository):
        bob = _author(db_session, api_repository, "bob@example.com")
        response = client.get(
            f"{API}/repositories/{api_repository.id}/metrics/files",
            params={"author": bob.id},
            headers=_headers(registered_user),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["commit_count"] == 2
        assert {item["path"] for item in body["files"]} == {"feature.txt", "readme.txt"}

    def test_commit_param(self, client, registered_user, api_repository, scripted_repo):
        response = client.get(
            f"{API}/repositories/{api_repository.id}/metrics/files",
            params=[("commit", scripted_repo.shas["c2"])],
            headers=_headers(registered_user),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["commit_count"] == 1

    def test_reference_param(self, client, registered_user, api_repository, scripted_repo):
        response = client.get(
            f"{API}/repositories/{api_repository.id}/metrics/directories",
            params={"reference": scripted_repo.shas["c2"]},
            headers=_headers(registered_user),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["commit_count"] == 2
        assert {item["path"] for item in body["directories"]} == {"", "sub"}

    def test_path_param(self, client, registered_user, api_repository):
        response = client.get(
            f"{API}/repositories/{api_repository.id}/metrics/files",
            params={"path": "sub"},
            headers=_headers(registered_user),
        )
        assert response.status_code == 200, response.text
        assert {item["path"] for item in response.json()["files"]} == {
            "sub/b.txt",
            "sub/c.txt",
            "sub/d.txt",
        }

    def test_unknown_reference_returns_400(
        self, client, registered_user, api_repository
    ):
        response = client.get(
            f"{API}/repositories/{api_repository.id}/metrics/files",
            params={"reference": "0" * 40},
            headers=_headers(registered_user),
        )
        assert response.status_code == 400

    def test_missing_repository_returns_404(self, client, registered_user):
        response = client.get(
            f"{API}/repositories/999999/metrics/files",
            headers=_headers(registered_user),
        )
        assert response.status_code == 404

    def test_foreign_repository_returns_404(self, client, registered_user, api_repository):
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
        token = login.json()["access_token"]

        response = client.get(
            f"{API}/repositories/{api_repository.id}/metrics/files",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 404

    def test_requires_authentication(self, client, api_repository):
        response = client.get(f"{API}/repositories/{api_repository.id}/metrics/files")
        assert response.status_code == 401
