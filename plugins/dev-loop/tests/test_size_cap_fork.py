"""`dl ci size-cap` counts the branch's own lines: from the fork point, or the remembered parent."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from test_ci_steps import branch, git, project  # noqa: F401 - project is the fixture
from test_pr import Fake, fake, source_cfg  # noqa: F401 - fake is the fixture

from dev_loop import pr
from dev_loop.ci import size, steps

Out = pytest.CaptureFixture[str]


def lines(root: Path, name: str, count: int) -> None:
    (root / name).write_text("x = 1\n" * count, encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", name)


def test_lines_the_base_gained_after_the_fork_are_not_counted(
    project: Path, capfd: Out,  # noqa: F811
) -> None:
    git(project, "checkout", "-qb", "feature")
    lines(project, "mine.py", 20)
    git(project, "checkout", "-q", "main")
    lines(project, "theirs.py", 300)
    git(project, "checkout", "-q", "feature")
    assert steps.main(["size-cap", "--base", "main"]) == 0
    assert "size cap: 20 production lines" in capfd.readouterr().out


def test_a_base_refused_elsewhere_still_lends_its_size_section(
    project: Path, capfd: Out,  # noqa: F811
) -> None:
    (project / "dev-loop.yaml").write_text(
        "version: 1\nmanagers: {someone: {email: noreply@example.org, kind: person}}\n"
        "size: {cap: 50, advise: 20, production: ['*.py']}\n", encoding="utf-8")
    git(project, "commit", "-qam", "managers as a mapping")
    base = git(project, "rev-parse", "HEAD")
    branch(project, "version: 1\nsize: {cap: 1000, advise: 100, production: ['*.py']}\n",
           {"a.py": 60})
    assert steps.main(["size-cap", "--base", base]) == 1
    out = capfd.readouterr().out
    assert "60 production lines is over the 50-line cap" in out
    assert f"size cap: size section from {base} (the rest of its config refused: " in out


def test_a_step_with_no_base_measures_from_the_remembered_parent(
    project: Path, capfd: Out, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    monkeypatch.delenv("PREPUSH_BASE", raising=False)
    git(project, "checkout", "-qb", "parent")
    lines(project, "parent.py", 150)
    git(project, "update-ref", "refs/remotes/origin/parent", "HEAD")
    git(project, "checkout", "-qb", "child")
    lines(project, "child.py", 40)
    git(project, "config", "branch.child.dl-base", "parent")
    assert steps.main(["size-cap"]) == 0
    assert "size cap: 40 production lines" in capfd.readouterr().out


def test_a_step_falls_back_when_the_remote_lost_the_parent(
    project: Path, capfd: Out, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    monkeypatch.delenv("PREPUSH_BASE", raising=False)
    git(project, "init", "-q", "--bare", "origin.git")
    git(project, "remote", "add", "origin", str(project / "origin.git"))
    git(project, "push", "-q", "origin", "main")
    git(project, "checkout", "-qb", "parent")
    lines(project, "parent.py", 150)
    git(project, "update-ref", "refs/remotes/origin/parent", "HEAD")  # stale: gone from origin
    git(project, "checkout", "-qb", "child")
    lines(project, "child.py", 40)
    git(project, "config", "branch.child.dl-base", "parent")
    assert steps.main(["size-cap"]) == 1
    out, err = capfd.readouterr()
    assert "190 production lines is over the 50-line cap" in out
    assert "origin has no branch parent now" in err


def test_an_unreachable_remote_keeps_the_parent_within_the_timeout(
    project: Path, capfd: Out, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    monkeypatch.delenv("PREPUSH_BASE", raising=False)
    monkeypatch.setattr(size, "LS_REMOTE_TIMEOUT", 2)
    git(project, "config", "protocol.ext.allow", "always")
    git(project, "remote", "add", "origin", "ext::sleep 60")
    git(project, "checkout", "-qb", "parent")
    lines(project, "parent.py", 150)
    git(project, "update-ref", "refs/remotes/origin/parent", "HEAD")
    git(project, "checkout", "-qb", "child")
    lines(project, "child.py", 40)
    git(project, "config", "branch.child.dl-base", "parent")
    start = time.monotonic()
    assert steps.main(["size-cap"]) == 0  # the parent is kept, never silently forgotten
    assert time.monotonic() - start < 2 + 5
    out, err = capfd.readouterr()
    assert "size cap: 40 production lines" in out
    assert "could not reach origin" in err


def test_the_hook_waits_on_the_remote_as_long_as_dl_ci_does() -> None:
    hook = Path(steps.__file__).with_name("prepush.sh").read_text(encoding="utf-8")
    assert f"ls_remote_timeout={size.LS_REMOTE_TIMEOUT}  #" in hook
    assert "GIT_TERMINAL_PROMPT=0 timeout \"$ls_remote_timeout\" git ls-remote" in hook


def test_the_stray_commit_span_starts_at_the_named_base(
    fake: Fake, tmp_path: Path,  # noqa: F811
) -> None:
    source_cfg(tmp_path)
    assert pr.main(["c1", "--base", "dev-loop/c0"]) == 0
    log = next(cmd for cmd in fake.commands if cmd[:2] == ["git", "log"])
    assert "origin/dev-loop/c0..HEAD" in log  # the parent's task commits are not this part's
    assert ["git", "fetch", "origin", "dev-loop/c0"] in fake.commands
    assert ["git", "config", "branch.dev-loop/c1.dl-base", "dev-loop/c0"] in fake.commands
    assert ["git", "rebase", "--rebase-merges", "origin/dev-loop/c0"] in fake.commands
    assert ["git", "diff", "--numstat", "--no-renames", "origin/dev-loop/c0", "HEAD"] in (
        fake.commands)
    assert fake.created[0][1] == "trunk"
