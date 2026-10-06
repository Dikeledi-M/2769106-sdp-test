"""Tests for the metrics engine (file / directory / repository / author) and
the metrics API.

Expected values are hand-derived from the scripted history documented in
``tests/git_history.py``; commit set boundary cases follow the spec:

* ``H_t`` is inclusive of ``t``, ``H_i,j`` is inclusive of ``i`` and
  exclusive of ``j`` (committer dates);
* ``H-bar`` excludes merge commits;
* renames attribute their changes to the new path, deletions to the
  deleted path, and binary files are never measured;
* pure renames (zero churn) are never counted as modifications.
"""
import pytest
from sqlalchemy import select

from app.models.author import Author
from app.models.commit import Commit, CommitFile
from app.models.repository import Repository
from app.services.metrics import (
    MetricsError,
    MetricsFilter,
    author_metrics,
    directory_metrics,
    file_metrics,
    repository_metrics,
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


class TestFileModifications:
    def test_counts_commits_with_nonzero_churn(self, db_session, ingested_repository):
        result = file_metrics(db_session, ingested_repository)
        by_path = {obj.path: obj for obj in result.objects}
        assert by_path["a.txt"].modifications == 2  # c1 and c2
        assert by_path["sub/b.txt"].modifications == 2  # c1 and c2
        assert by_path["sub/d.txt"].modifications == 2  # c4 and c5
        assert by_path["feature.txt"].modifications == 1
        assert by_path["readme.txt"].modifications == 1

    def test_pure_rename_is_not_a_modification(self, db_session, ingested_repository):
        result = file_metrics(db_session, ingested_repository)
        by_path = {obj.path: obj for obj in result.objects}
        assert by_path["sub/c.txt"].modifications == 0
        assert by_path["sub/c.txt"].churn == 0
        assert by_path["sub/c.txt"].modification_frequency == 0.0
        assert by_path["sub/c.txt"].churn_rate == 0.0

    def test_frequency_and_churn_rate(self, db_session, ingested_repository):
        result = file_metrics(db_session, ingested_repository)
        by_path = {obj.path: obj for obj in result.objects}
        assert by_path["a.txt"].modification_frequency == pytest.approx(2 / 7)
        assert by_path["a.txt"].churn_rate == pytest.approx(15 / 7)
        assert by_path["sub/d.txt"].churn_rate == pytest.approx(10 / 7)
        for obj in result.objects:  # rates are per-commit averages
            assert obj.modification_frequency == pytest.approx(
                obj.modifications / result.commit_count
            )
            assert obj.churn_rate == pytest.approx(obj.churn / result.commit_count)

    def test_rates_over_smaller_commit_set(self, db_session, ingested_repository):
        filters = MetricsFilter(from_ts=dt(C1_TIME), to_ts=dt(C3_TIME))  # c1, c2
        result = file_metrics(db_session, ingested_repository, filters)
        by_path = {obj.path: obj for obj in result.objects}
        assert by_path["a.txt"].modification_frequency == pytest.approx(1.0)
        assert by_path["a.txt"].churn_rate == pytest.approx(7.5)
        assert by_path["sub/b.txt"].churn_rate == pytest.approx(3.0)


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


class TestDirectoryModifications:
    def test_root_counts_each_commit_once(self, db_session, ingested_repository):
        result = directory_metrics(db_session, ingested_repository)
        by_path = {obj.path: obj for obj in result.objects}
        root = by_path[""]
        assert root.modifications == 6  # c1, c2, c4, c5, c6, feature (c3 is a rename)
        assert root.modification_frequency == pytest.approx(6 / 7)
        assert root.churn_rate == pytest.approx(37 / 7)

    def test_subdirectory_modifications(self, db_session, ingested_repository):
        result = directory_metrics(db_session, ingested_repository)
        by_path = {obj.path: obj for obj in result.objects}
        sub = by_path["sub"]
        assert sub.modifications == 4  # c1, c2, c4, c5
        assert sub.modification_frequency == pytest.approx(4 / 7)
        assert sub.churn_rate == pytest.approx(16 / 7)


class TestRepositoryMetrics:
    def test_root_of_the_commit_tree(self, db_session, ingested_repository):
        result = repository_metrics(db_session, ingested_repository)
        assert result.commit_count == 7
        root = result.root
        assert root.path == ""
        assert (root.added_lines, root.removed_lines) == (27, 10)
        assert (root.growth, root.churn) == (17, 37)
        assert root.modifications == 6
        assert root.modification_frequency == pytest.approx(6 / 7)
        assert root.churn_rate == pytest.approx(37 / 7)

    def test_matches_root_directory_metrics(self, db_session, ingested_repository):
        result = repository_metrics(db_session, ingested_repository)
        directories = directory_metrics(db_session, ingested_repository)
        root = next(obj for obj in directories.objects if obj.path == "")
        assert result.root == root

    def test_time_range(self, db_session, ingested_repository):
        filters = MetricsFilter(from_ts=dt(C1_TIME), to_ts=dt(C3_TIME))  # c1, c2
        result = repository_metrics(db_session, ingested_repository, filters)
        assert result.commit_count == 2
        assert (result.root.added_lines, result.root.removed_lines) == (19, 2)
        assert (result.root.growth, result.root.churn) == (17, 21)
        assert result.root.modifications == 2
        assert result.root.churn_rate == pytest.approx(10.5)

    def test_empty_commit_set_is_all_zero(self, db_session, ingested_repository):
        filters = MetricsFilter(from_ts=dt("2026-01-02T00:00:00+00:00"))
        result = repository_metrics(db_session, ingested_repository, filters)
        assert result.commit_count == 0
        assert result.root.path == ""
        assert (result.root.added_lines, result.root.removed_lines) == (0, 0)
        assert (result.root.growth, result.root.churn) == (0, 0)
        assert result.root.modifications == 0
        assert result.root.modification_frequency == 0.0
        assert result.root.churn_rate == 0.0


class TestAuthorMetrics:
    def test_full_commit_set(self, db_session, ingested_repository):
        result = author_metrics(db_session, ingested_repository)
        assert result.commit_count == 7
        assert result.path == ""
        assert [author.display_name for author in result.authors] == ["Alice", "Bob"]
        alice, bob = result.authors
        assert (alice.churn, alice.modifications) == (31, 4)  # c3 is a pure rename
        assert (bob.churn, bob.modifications) == (6, 2)
        assert alice.ownership == pytest.approx(31 / 37)
        assert bob.ownership == pytest.approx(6 / 37)

    def test_ownership_sums_to_one(self, db_session, ingested_repository):
        result = author_metrics(db_session, ingested_repository)
        assert sum(author.ownership for author in result.authors) == pytest.approx(1.0)

    def test_author_ids_are_canonical(self, db_session, ingested_repository):
        alice = _author(db_session, ingested_repository, "alice@example.com")
        result = author_metrics(db_session, ingested_repository)
        assert result.authors[0].author_id == alice.id

    def test_directory_scope(self, db_session, ingested_repository):
        result = author_metrics(db_session, ingested_repository, MetricsFilter(path="sub"))
        assert result.path == "sub"
        assert len(result.authors) == 1
        alice = result.authors[0]
        assert alice.display_name == "Alice"
        assert (alice.churn, alice.modifications) == (16, 4)
        assert alice.ownership == pytest.approx(1.0)

    def test_file_scope(self, db_session, ingested_repository):
        filters = MetricsFilter(path="sub/d.txt")
        result = author_metrics(db_session, ingested_repository, filters)
        assert result.path == "sub/d.txt"
        assert len(result.authors) == 1
        assert (result.authors[0].churn, result.authors[0].modifications) == (10, 2)

    def test_author_filter_restricts_the_commit_set(self, db_session, ingested_repository):
        bob = _author(db_session, ingested_repository, "bob@example.com")
        filters = MetricsFilter(author_ids=(bob.id,))
        result = author_metrics(db_session, ingested_repository, filters)
        assert result.commit_count == 2
        assert [(author.display_name, author.churn) for author in result.authors] == [
            ("Bob", 6)
        ]
        assert result.authors[0].ownership == pytest.approx(1.0)

    def test_empty_commit_set(self, db_session, ingested_repository):
        filters = MetricsFilter(from_ts=dt("2026-01-02T00:00:00+00:00"))
        result = author_metrics(db_session, ingested_repository, filters)
        assert result.commit_count == 0
        assert result.authors == []


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

    def test_file_metrics_expose_rates(self, client, registered_user, api_repository):
        response = client.get(
            f"{API}/repositories/{api_repository.id}/metrics/files",
            headers=_headers(registered_user),
        )
        assert response.status_code == 200, response.text
        a_txt = next(
            item for item in response.json()["files"] if item["path"] == "a.txt"
        )
        assert a_txt["modifications"] == 2
        assert a_txt["modification_frequency"] == pytest.approx(2 / 7)
        assert a_txt["churn_rate"] == pytest.approx(15 / 7)

    def test_repository_metrics_endpoint(self, client, registered_user, api_repository):
        response = client.get(
            f"{API}/repositories/{api_repository.id}/metrics/repository",
            headers=_headers(registered_user),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["commit_count"] == 7
        root = body["repository"]
        assert root["path"] == ""
        assert (root["added_lines"], root["removed_lines"]) == (27, 10)
        assert (root["growth"], root["churn"]) == (17, 37)
        assert root["modifications"] == 6
        assert root["churn_rate"] == pytest.approx(37 / 7)

    def test_author_metrics_endpoint(self, client, registered_user, api_repository):
        response = client.get(
            f"{API}/repositories/{api_repository.id}/metrics/authors",
            headers=_headers(registered_user),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["commit_count"] == 7
        assert body["path"] == ""
        assert [author["display_name"] for author in body["authors"]] == ["Alice", "Bob"]
        alice, bob = body["authors"]
        assert (alice["churn"], alice["modifications"]) == (31, 4)
        assert alice["ownership"] == pytest.approx(31 / 37)
        assert bob["ownership"] == pytest.approx(6 / 37)

    def test_author_metrics_path_param(self, client, registered_user, api_repository):
        response = client.get(
            f"{API}/repositories/{api_repository.id}/metrics/authors",
            params={"path": "sub"},
            headers=_headers(registered_user),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["path"] == "sub"
        assert [author["display_name"] for author in body["authors"]] == ["Alice"]
        assert body["authors"][0]["churn"] == 16

    def test_new_endpoints_require_authentication(self, client, api_repository):
        for suffix in ("repository", "authors"):
            response = client.get(
                f"{API}/repositories/{api_repository.id}/metrics/{suffix}"
            )
            assert response.status_code == 401
