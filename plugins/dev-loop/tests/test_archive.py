"""dl archive: a merged change moves by git mv; an unmerged one is refused."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from dev_loop import archive as mod
from dev_loop.records.record import faults
from test_neutral import KNOWN, check

PLUGIN = Path(__file__).resolve().parents[1]
FILES = ["src/dev_loop/archive.py", "skills/archive/SKILL.md"]


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "noreply@example.com")
    git(tmp_path, "config", "user.name", "a")
    folder = tmp_path / ".dev-loop/changes/demo"
    folder.mkdir(parents=True)
    (folder / "tasks.md").write_text("# demo\n", encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "init")
    bare = tmp_path.parent / f"{tmp_path.name}-remote.git"
    git(tmp_path, "init", "-q", "--bare", str(bare))
    git(tmp_path, "remote", "add", "origin", str(bare))
    git(tmp_path, "push", "-q", "origin", "main")
    return tmp_path


def test_merged_change_moves_with_one_record(repo: Path) -> None:
    git(repo, "checkout", "-q", "-b", "dev-loop/demo")  # at main's tip: merged
    target = mod.archive(repo, "demo")
    assert target.parent == repo / ".dev-loop/changes/archive"
    assert target.name.endswith("-demo") and len(target.name) == len("2026-01-01-demo")
    assert not (repo / ".dev-loop/changes/demo").exists()
    assert (target / "tasks.md").is_file()
    lines = (target / "records.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["kind"] == "archive"
    to = target.relative_to(repo).as_posix()
    assert (record["from"], record["to"]) == (".dev-loop/changes/demo", to)
    assert record["line"] in (target / "status.md").read_text(encoding="utf-8")
    assert faults(record) == []
    assert mod.main(["demo"]) == 1  # gone now


def test_unmerged_branch_is_refused(repo: Path) -> None:
    git(repo, "checkout", "-q", "-b", "dev-loop/demo")
    (repo / "x.txt").write_text("x", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "work")
    with pytest.raises(ValueError, match="not merged"):
        mod.archive(repo, "demo")
    assert (repo / ".dev-loop/changes/demo").is_dir()


def test_archive_after_land_with_local_main_behind(repo: Path) -> None:
    git(repo, "checkout", "-q", "-b", "dev-loop/demo")
    (repo / "x.txt").write_text("x", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "work")
    git(repo, "push", "-q", "origin", "dev-loop/demo:main")  # the land: remote main moves
    assert mod.archive(repo, "demo").name.endswith("-demo")


def test_fetch_failure_is_not_run(repo: Path) -> None:
    git(repo, "checkout", "-q", "-b", "dev-loop/demo")
    git(repo, "remote", "set-url", "origin", str(repo / "missing.git"))
    assert mod.main(["demo"]) == 1
    with pytest.raises(ValueError, match="fetch .* failed: not run"):
        mod.archive(repo, "demo")
    assert (repo / ".dev-loop/changes/demo").is_dir()


def _remote_tree(repo: Path) -> str:
    git(repo, "fetch", "-q", "origin")
    return subprocess.run(["git", "ls-tree", "-r", "--name-only", "origin/main"], cwd=repo,
                          check=True, capture_output=True, text=True).stdout


def test_archive_lands_on_the_remote_integration(repo: Path) -> None:
    git(repo, "checkout", "-q", "-b", "dev-loop/demo")
    (repo / ".dev-loop/changes/demo/proposal.md").write_text("p\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "work")
    git(repo, "push", "-q", "origin", "dev-loop/demo:main")
    assert mod.main(["demo"]) == 0
    tree = _remote_tree(repo)
    assert ".dev-loop/changes/demo/" not in tree
    assert any(n.endswith("-demo/proposal.md") and "/archive/" in n for n in tree.split())


def test_refused_from_the_integration_checkout(repo: Path) -> None:
    git(repo, "branch", "dev-loop/demo")
    with pytest.raises(ValueError, match="from the change's worktree"):
        mod.archive(repo, "demo")
    assert (repo / ".dev-loop/changes/demo").is_dir()


def test_refused_when_the_diff_touches_src(repo: Path) -> None:
    git(repo, "checkout", "-q", "-b", "dev-loop/demo")
    (repo / "src").mkdir()
    (repo / "src/x.py").write_text("x = 1\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "work")
    git(repo, "push", "-q", "origin", "dev-loop/demo:main")
    (repo / "src/x.py").write_text("x = 2\n", encoding="utf-8")
    git(repo, "add", "src/x.py")  # staged, so the archive commit would carry it
    with pytest.raises(ValueError, match="outside"):
        mod.archive(repo, "demo")


def test_bad_slug_is_refused(repo: Path) -> None:
    for slug in ("..", "a/b", "nope"):
        with pytest.raises(ValueError, match="no change"):
            mod.archive(repo, slug)


def test_files_are_neutral() -> None:
    assert check([PLUGIN / f for f in FILES]) == []
    assert not [f for f in FILES if f"plugins/dev-loop/{f}" in KNOWN]


def test_a_rejected_push_says_integration_moved(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git(repo, "checkout", "-q", "-b", "dev-loop/demo")
    git(repo, "push", "-q", "origin", "dev-loop/demo:main")  # the land
    other = tmp_path.parent / f"{tmp_path.name}-other"
    remote = str(tmp_path.parent / f"{tmp_path.name}-remote.git")
    git(tmp_path, "clone", "-q", "-b", "main", remote, str(other))
    git(other, "config", "user.email", "noreply@example.com")
    git(other, "config", "user.name", "b")
    (other / "y.txt").write_text("y", encoding="utf-8")
    git(other, "add", "-A")
    git(other, "commit", "-qm", "moved")
    real = mod._git

    def racing(root: Path, *args: str, **kw: int) -> subprocess.CompletedProcess[str]:
        if args[0] == "push":  # another push lands between the fetch and ours
            git(other, "push", "-q", "origin", "HEAD:main")
        return real(root, *args, **kw)

    monkeypatch.setattr(mod, "_git", racing)
    with pytest.raises(ValueError, match="integration moved: run dl archive again"):
        mod.archive(repo, "demo")
    assert "/archive/" not in _remote_tree(repo)
