"""Supervision rules for `dl clean` (section 4): live runs, squash merges, inode and /tmp pressure.

Temp git repos and a fake home with run folders — never the real system, never a process by name.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from test_neutral import check

from dev_loop import clean, config, supervise


def sh(*args: str, cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


def _commit(repo: Path, name: str) -> None:
    (repo / name).write_text("x\n", encoding="utf-8")
    sh("git", "add", "-A", cwd=repo)
    sh("git", "commit", "-qm", name, cwd=repo)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A checkout on `main` with a bare origin (main pushed); cwd is the checkout, as a worker runs."""
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    origin = tmp_path / "origin.git"
    sh("git", "init", "-q", "--bare", str(origin), cwd=tmp_path)
    root = tmp_path / "proj"
    root.mkdir()
    sh("git", "init", "-q", "-b", "main", ".", cwd=root)
    sh("git", "config", "user.email", "t@t", cwd=root)
    sh("git", "config", "user.name", "t", cwd=root)
    sh("git", "remote", "add", "origin", str(origin), cwd=root)
    _commit(root, "base")
    sh("git", "push", "-q", "origin", "main", cwd=root)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.chdir(root)
    return root


def _run(home: Path, ident: str, pid: str) -> None:
    folder = home / ".dev-loop" / "worker-run" / ident
    folder.mkdir(parents=True)
    (folder / "pid").write_text(pid)


def test_live_runs_are_the_folders_with_a_living_pid(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _run(home, "249", str(os.getpid()))  # this test's own process: alive
    _run(home, "250", "0999999")  # a dead pid
    bad = home / ".dev-loop" / "worker-run" / "251"
    bad.mkdir(parents=True)  # no pid file at all
    assert supervise.live_runs(home) == {"249"}


def test_a_worktree_a_live_run_holds_is_kept_though_merged(repo: Path) -> None:
    wt = repo.parent / "proj-wt" / "251"
    sh("git", "worktree", "add", "-q", "-b", "dev-loop/251", str(wt), "main", cwd=repo)
    loaded = config.load(repo)
    held = clean.worktrees(repo, loaded, {"251"})  # a live run named for this worktree
    free = clean.worktrees(repo, loaded, set())
    assert not held[0].remove and held[0].reason == "a live worker run"
    assert free[0].remove  # merged into main, nothing holding it


def test_merged_covers_ancestor_squash_and_rejects_unmerged(repo: Path) -> None:
    sh("git", "checkout", "-q", "-b", "dev-loop/ff", cwd=repo)  # fast-forward-style ancestor
    _commit(repo, "ff")
    sh("git", "checkout", "-q", "main", cwd=repo)
    sh("git", "merge", "-q", "--ff", "dev-loop/ff", cwd=repo)
    sh("git", "checkout", "-q", "-b", "dev-loop/squash", cwd=repo)
    _commit(repo, "s1")
    _commit(repo, "s2")
    sh("git", "checkout", "-q", "main", cwd=repo)
    sh("git", "merge", "-q", "--squash", "dev-loop/squash", cwd=repo)
    sh("git", "commit", "-qm", "squashed", cwd=repo)
    sh("git", "checkout", "-q", "-b", "dev-loop/open", cwd=repo)
    _commit(repo, "open")
    assert supervise.merged(repo, "dev-loop/ff", "main")
    assert supervise.merged(repo, "dev-loop/squash", "main")  # patch already in main
    assert not supervise.merged(repo, "dev-loop/open", "main")


def test_branches_counts_a_squash_merged_branch_as_a_candidate(repo: Path) -> None:
    sh("git", "checkout", "-q", "-b", "dev-loop/squash", cwd=repo)
    _commit(repo, "s1")
    sh("git", "checkout", "-q", "main", cwd=repo)
    sh("git", "merge", "-q", "--squash", "dev-loop/squash", cwd=repo)
    sh("git", "commit", "-qm", "squashed", cwd=repo)
    sh("git", "push", "-q", "origin", "main", cwd=repo)  # the integration ref now holds the squash
    by_name = {i.target: i for i in clean.branches(repo, config.load(repo))}
    assert by_name["dev-loop/squash"].remove and by_name["dev-loop/squash"].reason.startswith("merged")


def test_busiest_gauge_spans_roots_inodes_and_tmp(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (repo.parent / "proj-wt").mkdir()
    monkeypatch.setattr(clean, "inode_pct", lambda _p: 0)
    monkeypatch.setattr(clean, "disk_pct", lambda p: 71 if "proj-wt" in str(p) else 5)
    assert clean.busiest_gauge(repo, config.load(repo)) == (f"{repo.parent / 'proj-wt'} disk", 71)
    monkeypatch.setattr(clean, "inode_pct", lambda p: 88 if "proj-wt" in str(p) else 0)
    name, pct = clean.busiest_gauge(repo, config.load(repo))  # inode pressure now wins
    assert pct == 88 and name.endswith("inodes")


def test_when_full_fires_on_a_full_temp_dir(repo: Path, monkeypatch: pytest.MonkeyPatch,
                                            capsys: pytest.CaptureFixture[str]) -> None:
    sh("git", "worktree", "add", "-q", "-b", "dev-loop/m", str(repo.parent / "proj-wt" / "m"),
       "main", cwd=repo)
    tmp = str(clean.tempfile.gettempdir())
    monkeypatch.setattr(clean, "disk_pct", lambda p: 95 if str(p) == tmp else 5)  # only /tmp is full
    monkeypatch.setattr(clean, "inode_pct", lambda _p: 0)
    assert clean.main(["--apply", "--when-full"]) == 0
    assert "removed" in capsys.readouterr().out  # the full temp dir triggered the clean


def test_inode_pct_is_zero_without_statvfs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(clean.os, "statvfs", raising=False)
    assert clean.inode_pct(Path.cwd()) == 0


def test_files_are_neutral() -> None:
    root = Path(__file__).resolve().parents[3]
    assert check([root / "plugins/dev-loop/src/dev_loop/supervise.py", Path(__file__)]) == []
