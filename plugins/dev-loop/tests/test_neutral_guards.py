"""B1, guards for whoever commits: the protected-branch pre-commit, `dl ci done-rule`, the `dl pr`
nudge, and every `dl ci` step the CI templates name exists."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from test_neutral import check

from dev_loop import pr
from dev_loop.ci import steps
from dev_loop.records import record

PLUGIN = Path(__file__).resolve().parents[1]
DL = PLUGIN / "bin" / "dl"
TEMPLATES = [PLUGIN / "templates" / "ci" / "azure" / "dev-loop-steps.yml",
             PLUGIN / "templates" / "ci" / "github" / "dev-loop.yml"]
WRITTEN = [Path(__file__), PLUGIN / "hooks" / "githooks" / "pre-commit",
           PLUGIN / "src" / "dev_loop" / "ci" / "steps.py", PLUGIN / "src" / "dev_loop" / "pr.py",
           *TEMPLATES]


def git(
    root: Path, *args: str, env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, env=env,
                          check=False)


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    stub = tmp_path / "stub"
    stub.mkdir()
    (stub / "dl").write_text(f'#!/bin/sh\nexec bash "{DL}" "$@"\n')
    (stub / "dl").chmod(0o755)
    out = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_PROJECT_DIR",)}
    return out | {"GIT_AUTHOR_NAME": "t", "GIT_COMMITTER_NAME": "t",
                  "GIT_AUTHOR_EMAIL": "noreply@example.com",
                  "GIT_COMMITTER_EMAIL": "noreply@example.com",
                  "PATH": f"{stub}{os.pathsep}{os.environ['PATH']}", "DL_PYTHON": sys.executable}


@pytest.fixture
def repo(tmp_path: Path, env: dict[str, str]) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    git(work, "init", "-q", "-b", "main")
    (work / "dev-loop.yaml").write_text("version: 1\nbranches: {protected: [frozen]}\n")
    git(work, "add", "-A")
    assert git(work, "commit", "-qm", "base", env=env).returncode == 0
    (work / ".githooks").mkdir()
    hook = "#!/usr/bin/env sh\nexec dl githook pre-commit\n"
    (work / ".githooks" / "pre-commit").write_text(hook)
    (work / ".githooks" / "pre-commit").chmod(0o755)
    git(work, "config", "core.hooksPath", ".githooks")
    return work


def commit(work: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    (work / "a.txt").write_text(f"{len(git(work, 'log', '--oneline').stdout)}\n")
    git(work, "add", "-A")
    return git(work, "commit", "-qm", "change", env=env)


@pytest.mark.parametrize("branch", ["main", "frozen"])
def test_a_commit_on_a_protected_branch_is_refused(
    repo: Path, env: dict[str, str], branch: str,
) -> None:
    if branch != "main":
        git(repo, "checkout", "-qb", branch)
    done = commit(repo, env)
    assert done.returncode != 0
    assert f"protected branch '{branch}'" in done.stderr
    assert "git switch -c" in done.stderr  # the way out: make a branch
    assert git(repo, "log", "--oneline").stdout.count("\n") == 1


def test_a_commit_on_a_feature_branch_is_allowed(repo: Path, env: dict[str, str]) -> None:
    git(repo, "checkout", "-qb", "feature/x")
    done = commit(repo, env)
    assert done.returncode == 0, done.stderr
    assert git(repo, "log", "--oneline").stdout.count("\n") == 2  # noqa: PLR2004


TICKED = "- [x] T1\n"


def change(root: Path, name: str, tasks: str, records: list[dict[str, object]]) -> Path:
    from dev_loop.records import record  # noqa: PLC0415

    folder = root / ".dev-loop" / "changes" / name
    folder.mkdir(parents=True)
    (folder / "tasks.md").write_text(tasks)
    for item in records:
        record.write(root, name, dict(item))
    return folder


GREEN = [
    {"kind": "round", "pr": 1, "round": 1, "base": "abc", "blocker": 0, "major": 0, "minor": 0,
     "fixed": 0, "deferred": 0, "active": 0, "turns": 1, "seconds": 1, "tokens_in": 1,
     "tokens_out": 1, "unmetered": 0},
    {"kind": "metrics", "pr": 1, "change": "c1", "rounds": 1, "per_round": "0/0/0",
     "total": "0/0/0", "lessons": 0},
    {"kind": "lessons-logged", "count": 0},
    {"kind": "verify", "result": "green", "head": "HEAD"},
    {"kind": "simplifier", "edits": "none"},
    {"kind": "skills", "skills": "tdd"},
]


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "-c", "user.name=t", "-c", "user.email=noreply@example.com", "commit", "-qm", "base",
        "--allow-empty")  # a verify head must resolve
    (tmp_path / "dev-loop.yaml").write_text("version: 1\n")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return tmp_path


def test_done_rule_red_lists_what_is_missing(
    project: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    change(project, "c1", TICKED, GREEN[:3])
    assert steps.main(["done-rule", "c1"]) == 1
    out = capsys.readouterr().out
    assert "c1" in out
    assert "a green verify record and verify-report.md" in out
    assert "a simplifier line" in out


def test_done_rule_green(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    folder = change(project, "c1", TICKED, GREEN)
    (folder / "verify-report.md").write_text("# verify\n")
    assert steps.main(["done-rule", "c1"]) == 0
    assert "c1: done" in capsys.readouterr().out


def test_done_rule_with_no_such_change_is_red(project: Path) -> None:
    assert steps.main(["done-rule", "nope"]) == 1


def test_done_rule_judges_the_changes_the_diff_touches(
    project: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    ident = ["-c", "user.email=noreply@example.com", "-c", "user.name=t"]
    git(project, "add", "-A")
    git(project, *ident, "commit", "-qm", "base")
    # A diff that touches no change judges nothing: not run, exit 1, never green
    assert steps.main(["done-rule", "--base", "HEAD"]) == 1
    assert "not run: no change judged" in capsys.readouterr().out
    change(project, "c1", TICKED, GREEN[:1])
    change(project, "c2", "- [ ] T1\n", [])
    git(project, "add", "-A")
    git(project, *ident, "commit", "-qm", "change")
    assert steps.main(["done-rule", "--base", "HEAD~1"]) == 1
    out = capsys.readouterr().out
    assert "c1: missing" in out
    assert "c2: tasks open" in out  # the guard's scope: judged once every box is ticked


def test_dl_pr_prints_the_review_loop_nudge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from dev_loop.hosts import ado  # noqa: PLC0415

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    (tmp_path / "dev-loop.yaml").write_text("version: 1\n")
    folder = tmp_path / ".dev-loop" / "changes" / "c1"
    folder.mkdir(parents=True)
    record.append(folder, "c1", {"kind": "approval", "by": "user message", "at": "now"})
    record.append(folder, "c1", {"kind": "simplifier", "edits": "none"})
    (folder / "spec.md").write_text("## Acceptance criteria\n- `dl x` prints y\n", "utf-8")
    branch =subprocess.CompletedProcess([], 0, "dev-loop/c1\n", "")
    clean = subprocess.CompletedProcess([], 0, "", "")  # git status: no tracked change
    monkeypatch.setattr(pr, "sh", lambda root, cmd, stdin=None:
                        clean if "status" in cmd else branch)
    monkeypatch.setattr(pr, "size_cap", lambda root, base: 0)
    monkeypatch.setattr(ado, "pr_for_branch", lambda name: None)
    monkeypatch.setattr(ado, "pr_create", lambda *args: 42)
    assert pr.main(["c1"]) == 0
    out, err = capsys.readouterr()
    assert out.strip().endswith("42")
    assert "[pr-loop]" in err
    assert "42" in err
    (tmp_path / "dev-loop.yaml").write_text("version: 1\nreview: {auto_trigger: false}\n")
    assert pr.main(["c1"]) == 0
    assert "[pr-loop]" not in capsys.readouterr().err


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.parent.name)
def test_every_ci_step_in_the_templates_exists(template: Path) -> None:
    named = re.findall(r"\bdl ci ([\w-]+)", template.read_text(encoding="utf-8"))
    assert "done-rule" in named
    assert [step for step in named if step not in steps.STEPS] == []


def test_written_files_are_neutral() -> None:
    assert check(WRITTEN) == []
