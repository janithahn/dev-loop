"""`dl ci size-cap` against its base: what a branch cannot change about its own cap."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_ci_steps import branch, git, project  # noqa: F401 - project is the fixture

from dev_loop.ci import steps

RAISED = "version: 1\nsize: {cap: 1000, advise: 100, production: ['*.py']}\n"
Out = pytest.CaptureFixture[str]


def no_config_base(root: Path) -> str:
    git(root, "rm", "-q", "dev-loop.yaml")
    git(root, "commit", "-qm", "no config")
    return git(root, "rev-parse", "HEAD")


def test_an_adoption_pr_that_bundles_code_is_counted(project: Path, capfd: Out) -> None:  # noqa: F811
    base = no_config_base(project)
    branch(project, RAISED, {"a.py": 300})
    assert steps.main(["size-cap", "--base", base]) == 1
    out = capfd.readouterr().out
    assert f"size cap: {base} has no dev-loop.yaml; using the stricter of the working " in out
    assert "default cap: 200; globs from the working tree" in out
    assert "300 production lines is over the 200-line cap" in out


def test_a_config_only_adoption_pr_counts_nothing(project: Path, capfd: Out) -> None:  # noqa: F811
    base = no_config_base(project)
    branch(project, RAISED, {})
    assert steps.main(["size-cap", "--base", base]) == 0
    out = capfd.readouterr().out
    assert "has no dev-loop.yaml; using the stricter of the working " in out
    assert "size cap: 0 production lines (cap 200)" in out


def test_the_environment_cannot_raise_the_cap(
    project: Path, capfd: Out, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    base = git(project, "rev-parse", "HEAD")
    branch(project, RAISED, {"a.py": 60})
    monkeypatch.setenv("SIZE_CAP", "99999")
    monkeypatch.setenv("SIZE_ADVISE", "99999")
    assert steps.main(["size-cap", "--base", base]) == 1
    assert "60 production lines is over the 50-line cap" in capfd.readouterr().out


def test_a_branch_cannot_raise_only_the_advice(project: Path, capfd: Out) -> None:  # noqa: F811
    base = git(project, "rev-parse", "HEAD")
    branch(project, "version: 1\nsize: {cap: 50, advise: 50, production: ['*.py']}\n",
           {"a.py": 30})
    assert steps.main(["size-cap", "--base", base]) == 0
    assert "30 production lines; over 20, consider a split." in capfd.readouterr().out


def test_a_branch_cannot_empty_the_production_globs(project: Path, capfd: Out) -> None:  # noqa: F811
    base = git(project, "rev-parse", "HEAD")
    branch(project, "version: 1\nsize: {cap: 50, advise: 20, production: []}\n", {"a.py": 60})
    assert steps.main(["size-cap", "--base", base]) == 1
    assert "60 production lines is over the 50-line cap" in capfd.readouterr().out
