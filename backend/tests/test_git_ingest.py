"""Tests for the git ingestion service, driven by the scripted history.

The repository layout and its expected diffs are documented in
``tests/git_history.py``.
"""
from datetime import datetime

import pytest
from sqlalchemy import func, select

from app.models.author import Author, AuthorIdentity
from app.models.commit import Commit, CommitFile
from app.models.repository import Repository, RepositoryStatus
from app.services.git_ingest import IngestionError, ingest_repository
from tests.git_history import ScriptedRepo, run_git


def _by_subject(db_session, repository: Repository, subject: str) -> Commit:
    return (
        db_session.execute(
            select(Commit).where(
                Commit.repository_id == repository.id, Commit.message == subject
            )
        )
        .scalars()
        .one()
    )


def _changes_by_path(commit: Commit) -> dict[str, CommitFile]:
    return {change.path: change for change in commit.files}


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


class TestRepositoryState:
    def test_ingestion_sets_ready_state_and_counters(
        self, ingested_repository, scripted_repo: ScriptedRepo
    ):
        assert ingested_repository.status == RepositoryStatus.READY
        assert ingested_repository.head_sha == scripted_repo.shas["merge"]
        assert ingested_repository.default_branch == "main"
        assert ingested_repository.commit_count == 8  # 7 + the merge commit
        assert ingested_repository.author_count == 2
        assert ingested_repository.error_message is None
        assert ingested_repository.last_ingested_at is not None

    def test_every_commit_is_stored(self, db_session, ingested_repository):
        count = db_session.execute(
            select(func.count())
            .select_from(Commit)
            .where(Commit.repository_id == ingested_repository.id)
        ).scalar_one()
        assert count == 8

    def test_committer_date_stored_as_utc(self, db_session, ingested_repository, scripted_repo):
        stored = db_session.execute(
            select(Commit.committed_at).where(Commit.sha == scripted_repo.shas["c1"])
        ).scalar_one()
        assert stored == datetime(2026, 1, 1, 0, 0)


class TestDiffStatistics:
    def test_initial_commit_records_every_file(self, db_session, ingested_repository):
        commit = _by_subject(db_session, ingested_repository, "c1 initial")
        changes = _changes_by_path(commit)

        assert changes["a.txt"].change_type == "A"
        assert (changes["a.txt"].insertions, changes["a.txt"].deletions) == (10, 0)
        assert changes["sub/b.txt"].change_type == "A"
        assert (changes["sub/b.txt"].insertions, changes["sub/b.txt"].deletions) == (5, 0)
        assert changes["logo.bin"].is_binary is True
        assert changes["logo.bin"].insertions is None
        assert changes["logo.bin"].deletions is None

        assert commit.insertions == 15  # the binary file contributes no lines
        assert commit.deletions == 0
        assert commit.files_changed == 3

    def test_pure_rename_keeps_zero_line_counts(self, db_session, ingested_repository):
        commit = _by_subject(db_session, ingested_repository, "c3 rename only")
        (change,) = commit.files
        assert change.change_type == "R"
        assert change.old_path == "sub/b.txt"
        assert change.path == "sub/c.txt"
        assert (change.insertions, change.deletions) == (0, 0)

    def test_rename_with_edit_attributed_to_new_path(self, db_session, ingested_repository):
        commit = _by_subject(db_session, ingested_repository, "c4 rename+edit")
        (change,) = commit.files
        assert change.change_type == "R"
        assert change.old_path == "sub/c.txt"
        assert change.path == "sub/d.txt"
        assert (change.insertions, change.deletions) == (2, 2)

    def test_deletion_recorded_on_deleted_path(self, db_session, ingested_repository):
        commit = _by_subject(db_session, ingested_repository, "c5 delete")
        (change,) = commit.files
        assert change.change_type == "D"
        assert change.path == "sub/d.txt"
        assert (change.insertions, change.deletions) == (0, 6)

    def test_binary_change_stored_without_line_counts(self, db_session, ingested_repository):
        commit = _by_subject(db_session, ingested_repository, "c6 bob")
        changes = _changes_by_path(commit)
        assert changes["readme.txt"].change_type == "A"
        assert (changes["readme.txt"].insertions, changes["readme.txt"].deletions) == (4, 0)
        assert changes["logo.bin"].change_type == "M"
        assert changes["logo.bin"].is_binary is True
        assert changes["logo.bin"].insertions is None
        assert commit.insertions == 4

    def test_merge_commit_stored_without_changes(self, db_session, ingested_repository):
        merge = _by_subject(db_session, ingested_repository, "c7 merge")
        assert merge.is_merge is True
        assert len(merge.parent_shas) == 2
        assert merge.files == []
        assert merge.files_changed == 0


class TestAuthors:
    def test_authors_grouped_by_email(self, db_session, ingested_repository):
        authors = (
            db_session.execute(
                select(Author).where(Author.repository_id == ingested_repository.id)
            )
            .scalars()
            .all()
        )
        assert {author.primary_email for author in authors} == {
            "alice@example.com",
            "bob@example.com",
        }

    def test_one_identity_per_raw_git_identity(self, db_session, ingested_repository):
        identities = (
            db_session.execute(
                select(AuthorIdentity).where(
                    AuthorIdentity.repository_id == ingested_repository.id
                )
            )
            .scalars()
            .all()
        )
        assert {(identity.name, identity.email) for identity in identities} == {
            ("Alice", "alice@example.com"),
            ("Bob", "bob@example.com"),
        }

    def test_commits_follow_the_canonical_author(self, db_session, ingested_repository):
        alice = _author(db_session, ingested_repository, "alice@example.com")
        alice_shas = (
            db_session.execute(select(Commit.sha).where(Commit.author_id == alice.id))
            .scalars()
            .all()
        )
        assert len(alice_shas) == 6  # c1..c5 plus the merge commit

        bob = _author(db_session, ingested_repository, "bob@example.com")
        bob_shas = (
            db_session.execute(select(Commit.sha).where(Commit.author_id == bob.id))
            .scalars()
            .all()
        )
        assert len(bob_shas) == 2  # c6 and the feature commit


class TestReingestion:
    def test_rerun_replaces_commits_without_duplicates(
        self, db_session, ingested_repository, scripted_repo: ScriptedRepo
    ):
        ingest_repository(db_session, ingested_repository, scripted_repo.path)

        def count(model) -> int:
            return db_session.execute(
                select(func.count())
                .select_from(model)
                .where(model.repository_id == ingested_repository.id)
            ).scalar_one()

        assert count(Commit) == 8
        assert count(CommitFile) == 11  # 3+2+1+1+1+2+1 diff rows + 0 for the merge
        assert count(Author) == 2
        assert count(AuthorIdentity) == 2
        assert ingested_repository.commit_count == 8


class TestErrors:
    def test_missing_path_marks_repository_error(self, db_session, repository, tmp_path):
        with pytest.raises(IngestionError, match="does not exist"):
            ingest_repository(db_session, repository, tmp_path / "missing")
        db_session.refresh(repository)
        assert repository.status == RepositoryStatus.ERROR
        assert repository.error_message

    def test_non_git_directory_marks_repository_error(self, db_session, repository, tmp_path):
        broken = tmp_path / "not-a-repo"
        broken.mkdir()
        with pytest.raises(IngestionError):
            ingest_repository(db_session, repository, broken)
        db_session.refresh(repository)
        assert repository.status == RepositoryStatus.ERROR

    def test_repository_without_commits_marks_error(self, db_session, repository, tmp_path):
        empty = tmp_path / "empty-repo"
        empty.mkdir()
        run_git(empty, "init", "-b", "main")
        with pytest.raises(IngestionError, match="no commits"):
            ingest_repository(db_session, repository, empty)
        db_session.refresh(repository)
        assert repository.status == RepositoryStatus.ERROR
        assert repository.error_message == "repository has no commits"
