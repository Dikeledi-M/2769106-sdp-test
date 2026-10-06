"""Builds the deterministic git repository used by the ingestion and metrics tests.

History (committer dates are hourly from 2026-01-01T00:00 UTC):

======  =====  =====  ======================================================
name    who    time   change
======  =====  =====  ======================================================
c1      Alice  00:00  initial: a.txt (10 lines), sub/b.txt (5), logo.bin
c2      Alice  01:00  a.txt +3/-2, sub/b.txt +1
c3      Alice  02:00  pure rename sub/b.txt -> sub/c.txt (0/0)
c4      Alice  03:00  rename sub/c.txt -> sub/d.txt, edit +2/-2
c5      Alice  04:00  delete sub/d.txt (0/-6)
c6      Bob    05:00  readme.txt +4, logo.bin modified (binary, no counts)
f1      Bob    05:30  feature branch off c2: feature.txt +2
m1      Alice  06:00  merge f1 into main (merge commit, no changes)
======  =====  =====  ======================================================

``logo.bin`` contains NUL bytes in both versions, so git's binary detection
always flags it. The expected metric values in ``test_metrics.py`` are
hand-derived from this table.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

C1_TIME = "2026-01-01T00:00:00+00:00"
C2_TIME = "2026-01-01T01:00:00+00:00"
C3_TIME = "2026-01-01T02:00:00+00:00"
C4_TIME = "2026-01-01T03:00:00+00:00"
C5_TIME = "2026-01-01T04:00:00+00:00"
C6_TIME = "2026-01-01T05:00:00+00:00"
FEATURE_TIME = "2026-01-01T05:30:00+00:00"
MERGE_TIME = "2026-01-01T06:00:00+00:00"

ALICE = ("Alice", "alice@example.com")
BOB = ("Bob", "bob@example.com")

LOGO_BYTES = bytes(range(64))  # includes a NUL byte -> binary for git


@dataclass
class ScriptedRepo:
    path: Path
    shas: dict[str, str]  # logical name -> commit sha


def dt(value: str) -> datetime:
    """Parse one of the exported timestamp constants."""
    return datetime.fromisoformat(value)


def run_git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    """Run git with the user's/system's configuration disabled."""
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    if env:
        environment.update(env)
    process = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, env=environment
    )
    if process.returncode != 0:
        stderr = process.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"git {' '.join(args)} failed: {stderr}")
    return process.stdout.decode("utf-8", errors="replace")


def _numbered_lines(count: int) -> str:
    return "".join(f"{number}\n" for number in range(1, count + 1))


def _write(repo: Path, relative: str, content: str | bytes) -> None:
    target = repo / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        target.write_bytes(content)
    else:
        target.write_text(content, encoding="utf-8")


def _stage_all(repo: Path) -> None:
    run_git(repo, "add", "-A")


def _commit(repo: Path, who: tuple[str, str], when: str, message: str) -> str:
    name, email = who
    run_git(
        repo,
        "commit",
        "-m",
        message,
        env={
            "GIT_AUTHOR_NAME": name,
            "GIT_AUTHOR_EMAIL": email,
            "GIT_COMMITTER_NAME": name,
            "GIT_COMMITTER_EMAIL": email,
            "GIT_AUTHOR_DATE": when,
            "GIT_COMMITTER_DATE": when,
        },
    )
    return run_git(repo, "rev-parse", "HEAD").strip()


def build_scripted_repo(base_dir: Path) -> ScriptedRepo:
    """Create the scripted history at ``base_dir`` and return its commit map."""
    repo = Path(base_dir)
    repo.mkdir(parents=True)
    run_git(repo, "init", "-b", "main")
    shas: dict[str, str] = {}

    # c1: initial commit
    _write(repo, "a.txt", _numbered_lines(10))
    _write(repo, "sub/b.txt", "b1\nb2\nb3\nb4\nb5\n")
    _write(repo, "logo.bin", LOGO_BYTES)
    _stage_all(repo)
    shas["c1"] = _commit(repo, ALICE, C1_TIME, "c1 initial")

    # c2: a.txt +3/-2, sub/b.txt +1
    _write(repo, "a.txt", _numbered_lines(8) + "x1\nx2\nx3\n")
    _write(repo, "sub/b.txt", "b1\nb2\nb3\nb4\nb5\nb6\n")
    _stage_all(repo)
    shas["c2"] = _commit(repo, ALICE, C2_TIME, "c2 modify")

    # c3: pure rename (100% similarity)
    run_git(repo, "mv", "sub/b.txt", "sub/c.txt")
    shas["c3"] = _commit(repo, ALICE, C3_TIME, "c3 rename only")

    # c4: rename + edit (+2/-2)
    run_git(repo, "mv", "sub/c.txt", "sub/d.txt")
    _write(repo, "sub/d.txt", "b1\nb2\nb3\nb4\nNEW1\nNEW2\n")
    _stage_all(repo)
    shas["c4"] = _commit(repo, ALICE, C4_TIME, "c4 rename+edit")

    # c5: delete sub/d.txt
    run_git(repo, "rm", "-q", "sub/d.txt")
    shas["c5"] = _commit(repo, ALICE, C5_TIME, "c5 delete")

    # c6: Bob adds readme.txt and modifies the binary
    _write(repo, "readme.txt", "r1\nr2\nr3\nr4\n")
    _write(repo, "logo.bin", bytes(reversed(LOGO_BYTES)))
    _stage_all(repo)
    shas["c6"] = _commit(repo, BOB, C6_TIME, "c6 bob")

    # feature branch off c2, merged back into main
    run_git(repo, "checkout", "-q", "-b", "feature", shas["c2"])
    _write(repo, "feature.txt", "f1\nf2\n")
    _stage_all(repo)
    shas["feature"] = _commit(repo, BOB, FEATURE_TIME, "feature commit")

    run_git(repo, "checkout", "-q", "main")
    name, email = ALICE
    run_git(
        repo,
        "merge",
        "-q",
        "--no-ff",
        "-m",
        "c7 merge",
        "feature",
        env={
            "GIT_AUTHOR_NAME": name,
            "GIT_AUTHOR_EMAIL": email,
            "GIT_COMMITTER_NAME": name,
            "GIT_COMMITTER_EMAIL": email,
            "GIT_AUTHOR_DATE": MERGE_TIME,
            "GIT_COMMITTER_DATE": MERGE_TIME,
        },
    )
    shas["merge"] = run_git(repo, "rev-parse", "HEAD").strip()

    return ScriptedRepo(path=repo, shas=shas)
