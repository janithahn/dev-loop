"""`dl pr` holds the branch to the base's size cap, the way `dl ci size-cap` does."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_ci_steps import PLUGIN, git

from dev_loop import pr

BASE = ("version: 1\nbranches: {integration: trunk}\n"
        "size: {cap: 50, advise: 20, production: ['*.py']}\n")
BRANCH = "dev-loop/c1"


@pytest.fixture
def work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A clone on the change's branch, its remote's trunk holding a 50-line cap."""
    root = tmp_path / "work"
    root.mkdir()
    git(tmp_path, "init", "-q", "--bare", "origin.git")
    git(root, "init", "-q", "-b", "trunk")
    git(root, "config", "user.email", "noreply@example.invalid")
    git(root, "config", "user.name", "t")
    (root / "dev-loop.yaml").write_text(BASE, encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "base")
    git(root, "remote", "add", "origin", str(tmp_path / "origin.git"))
    git(root, "push", "-q", "origin", "trunk")
    git(root, "checkout", "-qb", BRANCH)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(PLUGIN))
    monkeypatch.delenv("SIZE_CAP", raising=False)
    monkeypatch.delenv("SIZE_ADVISE", raising=False)
    return root


def commit(root: Path, lines: int, config: str = BASE) -> None:
    (root / "dev-loop.yaml").write_text(config, encoding="utf-8")
    (root / "a.py").write_text("x = 1\n" * lines, encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "work")


def pushed(root: Path) -> bool:
    return bool(git(root, "ls-remote", "origin", BRANCH))


def test_a_branch_under_the_cap_is_pushed(work: Path) -> None:
    commit(work, 10)
    assert pr.publish(work, BRANCH, "trunk") == 0
    assert pushed(work)


def test_a_branch_cannot_raise_its_own_cap(work: Path, capfd: pytest.CaptureFixture[str]) -> None:
    commit(work, 60, BASE.replace("cap: 50", "cap: 1000"))
    assert pr.publish(work, BRANCH, "trunk") == 1
    assert "60 production lines is over the 50-line cap" in capfd.readouterr().out
    assert not pushed(work)


def test_the_environment_cannot_raise_the_cap(
    work: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    commit(work, 60)
    monkeypatch.setenv("SIZE_CAP", "99999")
    assert pr.publish(work, BRANCH, "trunk") == 1
    assert not pushed(work)


def stacked(root: Path) -> None:
    """`parent` on the remote holds 150 lines over trunk; this branch adds 40 on top of it."""
    git(root, "checkout", "-qb", "parent")
    (root / "parent.py").write_text("x = 1\n" * 150, encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "parent")
    git(root, "push", "-q", "origin", "parent")
    git(root, "checkout", "-q", BRANCH)
    git(root, "merge", "-q", "parent")
    commit(root, 40)


def test_a_stacked_part_is_judged_from_its_parent_and_remembers_it(
    work: Path, capfd: pytest.CaptureFixture[str],
) -> None:
    stacked(work)
    assert pr.publish(work, BRANCH, "trunk", "parent") == 0
    assert pushed(work)
    assert git(work, "config", f"branch.{BRANCH}.dl-base") == "parent"
    assert "40 production lines" in capfd.readouterr().out
    assert pr.publish(work, BRANCH, "trunk") == 0  # a later push: the remembered parent
    assert "40 production lines" in capfd.readouterr().out


def test_a_parent_gone_from_the_remote_is_forgotten(
    work: Path, capfd: pytest.CaptureFixture[str],
) -> None:
    stacked(work)
    git(work, "config", f"branch.{BRANCH}.dl-base", "parent")
    git(work, "push", "-q", "origin", "--delete", "parent")
    assert pr.publish(work, BRANCH, "trunk") == 1
    out, err = capfd.readouterr()
    assert "190 production lines is over the 50-line cap" in out
    assert [line for line in err.splitlines() if "parent" in line] == [
        "dl pr: origin has no branch parent now; forgot it, judged from the target"]
    assert not git(work, "config", "--default", "", f"branch.{BRANCH}.dl-base")
    assert not pushed(work)


def test_a_stacked_part_is_judged_from_its_parent_after_the_target_moves(
    work: Path, capfd: pytest.CaptureFixture[str],
) -> None:
    stacked(work)
    git(work, "checkout", "-q", "trunk")
    (work / "trunk.py").write_text("x = 1\n" * 300, encoding="utf-8")
    git(work, "add", ".")
    git(work, "commit", "-qm", "trunk moves on")
    git(work, "push", "-q", "origin", "trunk")
    git(work, "checkout", "-q", BRANCH)
    assert pr.publish(work, BRANCH, "trunk", "parent") == 0
    assert "size cap: 40 production lines" in capfd.readouterr().out


@pytest.mark.parametrize("base", [BRANCH, "trunk"])
def test_a_base_of_this_or_a_protected_branch_is_refused(
    work: Path, capfd: pytest.CaptureFixture[str], base: str,
) -> None:
    protected = BASE.replace("{integration: trunk}", "{integration: trunk, protected: [trunk]}")
    commit(work, 40, protected)
    assert pr.publish(work, BRANCH, "trunk") == 0
    commit(work, 70, protected)  # 30 more: under the cap from its own push, over from trunk
    assert pr.publish(work, BRANCH, "trunk", base) == 1
    assert f"--base {base}" in capfd.readouterr().err
    assert git(work, "ls-remote", "origin", BRANCH).split()[0] != git(work, "rev-parse", "HEAD")
    assert not git(work, "config", "--default", "", f"branch.{BRANCH}.dl-base")
