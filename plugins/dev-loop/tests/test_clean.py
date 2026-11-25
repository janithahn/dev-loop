"""`dl clean`: safe removal of the loop's leftovers (C1).

Temp git repos with merged, unmerged, dirty, unpushed and branch-gone worktrees; run dirs live and
dead; logs; Docker and caches through fakes on PATH and a temp dir, never the real system.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest
from test_neutral import check

from dev_loop import clean, config


def sh(*args: str, cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


def _commit(wt: Path, name: str) -> None:
    (wt / name).write_text("x\n", encoding="utf-8")
    sh("git", "add", "-A", cwd=wt)
    sh("git", "commit", "-qm", name, cwd=wt)


@pytest.fixture
def proj(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A checkout on `main` with a bare origin; worktrees in each state under proj-wt."""
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
    monkeypatch.chdir(root)  # cwd is the checkout, as a worker runs clean
    return root


def add_wt(root: Path, name: str, branch: str) -> Path:
    wt = root.parent / "proj-wt" / name
    sh("git", "worktree", "add", "-q", "-b", branch, str(wt), "origin/main", cwd=root)
    return wt


def _loaded(root: Path) -> config.Loaded:
    return config.load(root)


def test_worktrees_candidate_and_kept_states(proj: Path) -> None:
    merged = add_wt(proj, "merged", "dev-loop/merged")
    dirty = add_wt(proj, "dirty", "dev-loop/dirty")
    (dirty / "scratch.txt").write_text("edit\n")  # uncommitted
    unpushed = add_wt(proj, "unpushed", "dev-loop/unpushed")
    _commit(unpushed, "local")  # committed, never pushed
    unmerged = add_wt(proj, "unmerged", "dev-loop/unmerged")
    _commit(unmerged, "ahead")
    sh("git", "push", "-q", "origin", "dev-loop/unmerged", cwd=unmerged)  # pushed, not merged
    gone = add_wt(proj, "gone", "dev-loop/gone")
    sh("git", "checkout", "-q", "--detach", cwd=gone)
    sh("git", "branch", "-qD", "dev-loop/gone", cwd=proj)

    by_path = {Path(i.target).name: i for i in clean.worktrees(proj, _loaded(proj))}
    assert by_path["merged"].remove and "merged into main" in by_path["merged"].reason
    assert by_path["gone"].remove and "branch gone" in by_path["gone"].reason
    assert not by_path["dirty"].remove and by_path["dirty"].reason == "uncommitted changes"
    assert not by_path["unpushed"].remove and by_path["unpushed"].reason == "unpushed commits"
    assert not by_path["unmerged"].remove and "not merged" in by_path["unmerged"].reason
    for name in ("merged", "dirty", "unpushed", "unmerged", "gone"):
        assert by_path[name].size >= 0
    _ = merged  # the merged worktree exists on disk until --apply removes it


def test_a_non_git_dir_under_a_root_is_kept_not_checked(proj: Path) -> None:
    stray = proj.parent / "proj-wt" / "stray"
    stray.mkdir(parents=True)
    (stray / "file").write_text("x")
    item = next(i for i in clean.worktrees(proj, _loaded(proj)) if Path(i.target).name == "stray")
    assert not item.remove and item.reason == "not checked"


def test_branches_merged_candidate_unmerged_kept_prefix_only(proj: Path) -> None:
    sh("git", "branch", "dev-loop/done", "origin/main", cwd=proj)  # merged, not checked out
    sh("git", "checkout", "-q", "-b", "dev-loop/wip", cwd=proj)
    _commit(proj, "wip")
    sh("git", "checkout", "-q", "main", cwd=proj)  # wip now unmerged, not checked out
    sh("git", "branch", "feature", "origin/main", cwd=proj)  # no prefix: ignored
    by_name = {i.target: i for i in clean.branches(proj, _loaded(proj))}
    assert by_name["dev-loop/done"].remove
    assert not by_name["dev-loop/wip"].remove and by_name["dev-loop/wip"].reason == "not merged"
    assert "feature" not in by_name


def _home_with_run(home: Path, ident: str, pid: str, *, days_old: float) -> Path:
    run = home / ".dev-loop" / "worker-run" / ident
    run.mkdir(parents=True)
    (run / "pid").write_text(pid)
    old = time.time() - days_old * 86400
    os.utime(run, (old, old))
    return run


def test_run_dirs_alive_old_recent_and_unchecked(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _home_with_run(home, "alive", str(os.getpid()), days_old=10)
    _home_with_run(home, "old", "0999999", days_old=5)  # dead pid, old
    _home_with_run(home, "recent", "0999999", days_old=0)  # dead pid, recent
    bad = _home_with_run(home, "bad", "0999999", days_old=10)
    (bad / "pid").write_text("not-a-pid")
    by = {Path(i.target).name: i for i in clean.run_dirs(home, 3)}
    assert not by["alive"].remove and "alive" in by["alive"].reason
    assert by["old"].remove and "finished over 3d ago" in by["old"].reason
    assert not by["recent"].remove and by["recent"].reason == "finished recently"
    assert not by["bad"].remove and by["bad"].reason == "not checked"


def test_logs_over_the_cap_are_rotated_newest_kept(tmp_path: Path) -> None:
    home = tmp_path / "home"
    base = home / ".dev-loop"
    base.mkdir(parents=True)
    big = base / "worker.log"
    big.write_text("x" * (2 * clean.MB))
    small = base / "small.log"
    small.write_text("tiny")
    by = {Path(i.target).name: i for i in clean.logs(home, 1)}
    assert by["worker.log"].remove and "rotated" in by["worker.log"].reason
    assert not by["small.log"].remove
    clean._rotate(big)  # keep the newest part as .1, the live log restarts
    assert not big.exists() and (base / "worker.log.1").exists()
    (base / "worker.log").write_text("fresh")
    clean._rotate(base / "worker.log")  # the one older .1 (the rest) goes
    assert (base / "worker.log.1").read_text() == "fresh"


def test_caches_older_than_keep_days_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    monkeypatch.setattr(clean.tempfile, "gettempdir", lambda: str(tmpdir))
    old = tmpdir / "vitest-abc"
    old.mkdir()
    (old / "f").write_text("x")
    os.utime(old, (time.time() - 10 * 86400,) * 2)
    recent = tmpdir / "pytest-of-user"
    recent.mkdir()
    other = tmpdir / "keepme"  # matches no glob
    other.mkdir()
    os.utime(other, (time.time() - 99 * 86400,) * 2)
    by = {Path(i.target).name: i for i in clean.caches(["vitest-*", "pytest-of-*"], 3)}
    assert by["vitest-abc"].remove
    assert not by["pytest-of-user"].remove
    assert "keepme" not in by


FAKE_DOCKER = """#!/bin/sh
case "$1 $2" in
  "volume ls") echo loop-vol-a; echo loop-vol-b ;;
  "ps -a") [ "$4" = "volume=loop-vol-b" ] && echo container9 ;;
  "image ls") echo img123 ;;
  "volume rm") echo "$3" >> "$ACT" ;;
  "image rm") echo "$3" >> "$ACT" ;;
esac
exit 0
"""


@pytest.fixture
def fake_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(FAKE_DOCKER)
    (bin_dir / "docker").chmod(0o755)
    act = tmp_path / "docker-actions"
    monkeypatch.setenv("ACT", str(act))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return act


def test_docker_volumes_in_use_are_kept_images_removed(fake_docker: Path) -> None:
    items = {i.target: i for i in clean.docker("acme")}
    assert items["loop-vol-a"].remove and items["loop-vol-a"].kind == "docker-volume"
    assert not items["loop-vol-b"].remove and items["loop-vol-b"].reason == "in use"
    assert items["img123"].remove and items["img123"].kind == "docker-image"
    for item in items.values():
        if item.remove:
            clean._remove(item, Path.cwd())
    done = fake_docker.read_text().split()
    assert "loop-vol-a" in done and "img123" in done and "loop-vol-b" not in done


def test_apply_removes_only_the_safe_ones_and_prunes(proj: Path) -> None:
    merged = add_wt(proj, "merged", "dev-loop/merged")
    dirty = add_wt(proj, "dirty", "dev-loop/dirty")
    (dirty / "scratch").write_text("edit")
    sh("git", "branch", "dev-loop/done", "origin/main", cwd=proj)
    loaded = _loaded(proj)
    items = clean.worktrees(proj, loaded) + clean.branches(proj, loaded)
    removed = clean.apply(items, proj, loaded)
    assert not merged.exists() and dirty.exists()
    assert {Path(r.target).name for r in removed} >= {"merged"}
    assert "dev-loop/done" in {r.target for r in removed}
    listed = subprocess.run(["git", "worktree", "list"], cwd=proj, capture_output=True, text=True)
    assert "merged" not in listed.stdout  # pruned
    heads = subprocess.run(["git", "branch", "--format=%(refname:short)"], cwd=proj,
                           capture_output=True, text=True).stdout.split()
    assert "dev-loop/done" not in heads and "dev-loop/merged" not in heads


def test_dry_run_lists_without_deleting(proj: Path, capsys: pytest.CaptureFixture[str]) -> None:
    merged = add_wt(proj, "merged", "dev-loop/merged")
    assert clean.main([]) == 0
    out = capsys.readouterr().out
    assert "would remove" in out and "candidate" in out
    assert merged.exists()  # a dry run deletes nothing


def test_apply_reports_and_empty_says_nothing(proj: Path, capsys: pytest.CaptureFixture[str]) -> None:
    add_wt(proj, "merged", "dev-loop/merged")
    assert clean.main(["--apply"]) == 0
    assert "removed 2 item(s)" in capsys.readouterr().out  # the worktree and its merged branch
    assert clean.main(["--apply"]) == 0  # nothing left
    assert "removed nothing" in capsys.readouterr().out


def test_json_output_shape(proj: Path, capsys: pytest.CaptureFixture[str]) -> None:
    add_wt(proj, "merged", "dev-loop/merged")
    assert clean.main(["--json"]) == 0
    import json

    data = json.loads(capsys.readouterr().out)
    assert data["applied"] is False and any(i["remove"] for i in data["items"])


def test_when_full_gate(proj: Path, monkeypatch: pytest.MonkeyPatch,
                        capsys: pytest.CaptureFixture[str]) -> None:
    add_wt(proj, "merged", "dev-loop/merged")
    monkeypatch.setattr(clean, "inode_pct", lambda _p: 0)  # disk alone drives this gate
    monkeypatch.setattr(clean, "disk_pct", lambda _p: 10)  # below the 80% threshold
    assert clean.main(["--apply", "--when-full"]) == 0
    assert capsys.readouterr().out == ""  # nothing done: disk is not full
    monkeypatch.setattr(clean, "disk_pct", lambda _p: 95)  # at or above the threshold
    assert clean.main(["--apply", "--when-full"]) == 0
    assert "removed nothing" not in capsys.readouterr().out


def _repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, remote: str | None,
          start: str = "main") -> Path:
    """A checkout on `start` with a first commit; a remote named `remote` (start pushed) if given.

    C1b dogfood: `dl clean` judged every branch "not merged" on this repo, whose remote is `hub`,
    not `origin` — `origin/main` did not exist, so every merge check failed (nothing was deleted).
    """
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    root = tmp_path / "proj"
    root.mkdir()
    sh("git", "init", "-q", "-b", start, ".", cwd=root)
    sh("git", "config", "user.email", "t@t", cwd=root)
    sh("git", "config", "user.name", "t", cwd=root)
    if remote:
        bare = tmp_path / "remote.git"
        sh("git", "init", "-q", "--bare", str(bare), cwd=tmp_path)
        sh("git", "remote", "add", remote, str(bare), cwd=root)
    _commit(root, "base")
    if remote:
        sh("git", "push", "-q", remote, start, cwd=root)
        sh("git", "fetch", "-q", remote, cwd=root)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.chdir(root)
    return root


def test_merged_judged_against_a_non_origin_remote(tmp_path: Path,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    root = _repo(tmp_path, monkeypatch, remote="hub")
    (root / "dev-loop.yaml").write_text("branches:\n  remote: hub\n", encoding="utf-8")
    sh("git", "branch", "dev-loop/done", "hub/main", cwd=root)  # merged, not checked out
    wt = root.parent / "proj-wt" / "done"
    sh("git", "worktree", "add", "-q", "-b", "dev-loop/wt", str(wt), "hub/main", cwd=root)
    by_name = {i.target: i for i in clean.branches(root, config.load(root))}
    assert by_name["dev-loop/done"].remove and "merged into main" in by_name["dev-loop/done"].reason
    by_path = {Path(i.target).name: i for i in clean.worktrees(root, config.load(root))}
    assert by_path["done"].remove and "merged into main" in by_path["done"].reason


def test_fall_back_to_local_integration_when_no_remote_ref(tmp_path: Path,
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    root = _repo(tmp_path, monkeypatch, remote=None)  # no origin/main; local main is the fallback
    sh("git", "branch", "dev-loop/done", "main", cwd=root)
    by_name = {i.target: i for i in clean.branches(root, config.load(root))}
    assert by_name["dev-loop/done"].remove and "merged into main" in by_name["dev-loop/done"].reason


def test_not_checked_and_summary_names_the_ref_when_neither_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _repo(tmp_path, monkeypatch, remote=None, start="work")  # no origin/main, no local main
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")  # keep the scan off the real home
    sh("git", "branch", "dev-loop/cand", "work", cwd=root)
    item = next(i for i in clean.branches(root, config.load(root)) if i.target == "dev-loop/cand")
    assert not item.remove and item.reason == "not checked: no origin/main or main"
    assert clean.main([]) == 0
    out = capsys.readouterr().out
    assert "merge status not checked — no origin/main or main" in out
    assert "not merged" not in out  # a missing integration ref never reports "not merged"


def test_files_are_neutral() -> None:
    root = Path(__file__).resolve().parents[3]
    assert check([root / "plugins/dev-loop/src/dev_loop/clean.py",
                  root / "plugins/dev-loop/hooks/session-end-clean.sh", Path(__file__)]) == []
