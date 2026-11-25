"""tools/release.sh and tools/merge.sh, run against temp repositories and bare remotes."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
TESTS = "plugins/dev-loop/tests"
GUARD = f"{TESTS}/test_neutral.py"
SCAN = "plugins/dev-loop/src/dev_loop/ci/secret_scan.py"
MANIFEST = "plugins/dev-loop/.claude-plugin/plugin.json"


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                          check=True)
    return done.stdout.strip()


def commit(repo: Path, files: dict[str, str], message: str) -> None:
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", message)


def new_repo(path: Path) -> Path:
    git(path.parent, "init", "-q", "-b", "main", path.name)
    git(path, "config", "user.email", "noreply@example.invalid")
    git(path, "config", "user.name", "t")
    for name in (GUARD, SCAN):
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / name, path / name)
    return path


def bare(path: Path) -> Path:
    git(path.parent, "init", "-q", "--bare", "-b", "main", path.name)
    return path


def run(tool: str, cwd: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(ROOT / "tools" / tool), *args], cwd=cwd, text=True,
                          env={**os.environ, **env}, capture_output=True, check=False)


@pytest.fixture
def private(tmp_path: Path) -> dict[str, str]:
    """A private name list outside the repository, holding one made-up name."""
    (tmp_path / "private.txt").write_text("# made-up names only\nzorblax\n", encoding="utf-8")
    return {"DEV_LOOP_PRIVATE_NAMES": str(tmp_path / "private.txt")}


@pytest.fixture
def source(tmp_path: Path) -> Path:
    repo = new_repo(tmp_path / "src")
    commit(repo, {MANIFEST: json.dumps({"version": "1.0.0"}), "README.md": "one\n"}, "base")
    return repo


def test_release_dry_run_then_fresh_then_on_top_then_refuses_the_tag(
    source: Path, tmp_path: Path, private: dict[str, str],
) -> None:
    remote = bare(tmp_path / "rel.git")
    env = {"RELEASE_REMOTE": str(remote), **private}
    dry = run("release.sh", source, env)
    assert dry.returncode == 0, dry.stdout + dry.stderr
    assert "dry run" in dry.stdout
    assert git(remote, "for-each-ref") == ""

    first = run("release.sh", source, env, "--push")
    assert first.returncode == 0, first.stdout + first.stderr
    assert git(remote, "rev-list", "--count", "main") == "1"
    assert git(remote, "rev-parse", "v1.0.0^{commit}") == git(remote, "rev-parse", "main")

    commit(source, {MANIFEST: json.dumps({"version": "1.0.1"}), "README.md": "two\n"}, "next")
    second = run("release.sh", source, env, "--push")
    assert second.returncode == 0, second.stdout + second.stderr
    assert git(remote, "rev-list", "--count", "main") == "2"
    assert git(remote, "show", "main:README.md") == "two"
    assert git(remote, "rev-parse", "main~1") == git(remote, "rev-parse", "v1.0.0^{commit}")

    again = run("release.sh", source, env, "--push")
    assert again.returncode == 1
    assert "v1.0.1 exists" in again.stdout + again.stderr


@pytest.mark.parametrize("planted", [
    "a host at " + ".".join(["10", "1", "2", "3"]),
    "a Zorblax bank",  # a name on the private list
    "key " + "AKIA" + "ABCDEFGHIJKLMNOP",
])
def test_release_refuses_a_name_an_address_or_a_secret(
    source: Path, tmp_path: Path, planted: str, private: dict[str, str],
) -> None:
    remote = bare(tmp_path / "rel.git")
    commit(source, {"notes.md": planted + "\n"}, "planted")
    done = run("release.sh", source, {"RELEASE_REMOTE": str(remote), **private}, "--push")
    assert done.returncode == 1
    assert "REFUSED" in done.stdout + done.stderr
    assert git(remote, "for-each-ref") == ""


def test_release_refuses_without_the_private_list(source: Path, tmp_path: Path) -> None:
    remote = bare(tmp_path / "rel.git")
    env = {"RELEASE_REMOTE": str(remote), "DEV_LOOP_PRIVATE_NAMES": str(tmp_path / "absent.txt")}
    done = run("release.sh", source, env, "--push")
    assert done.returncode == 1
    assert "REFUSED: no private name list" in done.stdout + done.stderr
    assert git(remote, "for-each-ref") == ""


def test_release_needs_a_remote(source: Path) -> None:
    assert run("release.sh", source, {"RELEASE_REMOTE": ""}).returncode != 0


@pytest.fixture
def merging(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    """A clone of a bare remote with branches a (a new test), b (conflicts), c (guard conflict)."""
    remote = bare(tmp_path / "hub.git")
    work = new_repo(tmp_path / "work")
    guard = (work / GUARD).read_text(encoding="utf-8")
    stale = guard.replace("KNOWN: list[str] = []", 'KNOWN: list[str] = [\n    "gone.md",\n]')
    commit(work, {GUARD: stale, "x.txt": "base\n"}, "base")
    git(work, "remote", "add", "origin", str(remote))
    for branch, files in {
        "a": {f"{TESTS}/test_a.py": "def test_a() -> None:\n    assert True\n"},
        "b": {"x.txt": "from b\n"},
        "c": {GUARD: stale.replace("Neutrality guard", "The guard"), "c.md": "c\n"},
    }.items():
        git(work, "checkout", "-q", "-b", branch, "main")
        commit(work, files, branch)
    git(work, "checkout", "-q", "main")
    commit(work, {"x.txt": "from main\n", GUARD: stale.replace("Neutrality", "Neutral")}, "main")
    git(work, "push", "-q", "origin", "main", "a", "b", "c")
    bin_dir = tmp_path / "bin"  # no uv on this PATH: the python3 -m pytest fallback runs
    bin_dir.mkdir()
    (bin_dir / "python3").write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    (bin_dir / "python3").chmod(0o755)
    # no private name list: merge.sh works without one, and the guard's private test skips
    absent = str(tmp_path / "absent.txt")
    return work, remote, {"PATH": f"{bin_dir}:/usr/bin:/bin", "DEV_LOOP_PRIVATE_NAMES": absent}


def test_merge_skips_the_conflict_takes_mains_guard_prunes_and_pushes(
    merging: tuple[Path, Path, dict[str, str]],
) -> None:
    work, remote, env = merging
    done = run("merge.sh", work, env, "a", "b", "c")
    out = done.stdout + done.stderr
    assert done.returncode == 0, out
    assert "CONFLICT b: x.txt" in out
    assert "merged: a c" in out
    assert "5 passed, 1 skipped" in out  # the guard's four tests, test_a; the private one skips
    log = git(remote, "log", "--format=%s", "main")
    assert "Merge a" in log
    assert "Merge c" in log
    assert "Merge b" not in log
    guard = git(remote, "show", f"main:{GUARD}")
    assert '"""Neutral guard' in guard
    assert "KNOWN: list[str] = []" in guard
    assert git(remote, "show", "main:x.txt") == "from main"


def test_merge_does_not_push_when_the_tests_fail(
    merging: tuple[Path, Path, dict[str, str]],
) -> None:
    work, remote, env = merging
    before = git(remote, "rev-parse", "main")
    git(work, "checkout", "-q", "-b", "d", "main")
    commit(work, {f"{TESTS}/test_d.py": "def test_d() -> None:\n    assert False\n"}, "d")
    git(work, "push", "-q", "origin", "d")
    git(work, "checkout", "-q", "main")
    done = run("merge.sh", work, env, "d")
    assert done.returncode == 1
    assert "TESTS FAILED" in done.stdout + done.stderr
    assert "1 failed" in done.stdout
    assert git(remote, "rev-parse", "main") == before
