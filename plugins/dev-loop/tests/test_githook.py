"""dl githook: a real `git push` in a temp repo runs the pre-push body; a failing step blocks it."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_neutral import check

PLUGIN = Path(__file__).resolve().parents[1]
DL = PLUGIN / "bin" / "dl"
WRITTEN = [
    "plugins/dev-loop/src/dev_loop/ci/githook.py",
    "plugins/dev-loop/src/dev_loop/ci/prepush.sh",
    "plugins/dev-loop/hooks/githooks/pre-commit",
    "plugins/dev-loop/hooks/githooks/pre-push",
    "plugins/dev-loop/tests/test_githook.py",
]
CONFIG = """branches: {integration: trunk}
gates: {sides: {core: "src/*", web: "web/*"}}
verify:
  runner: sh run.sh
  steps: {lint: "%s", web: "false"}
  prepush: [lint, web]
"""


def git(root: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, env=env, check=False)


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    """A stub `dl` that logs `dl ci <step>` and passes everything else to the real launcher."""
    stub = tmp_path / "stub"
    stub.mkdir()
    (stub / "dl").write_text(
        f'#!/bin/sh\nif [ "$1" = ci ]; then echo "$*" >> "{tmp_path}/ci.log"; exit 0; fi\n'
        f'exec bash "{DL}" "$@"\n'
    )
    (stub / "dl").chmod(0o755)
    out = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_PROJECT_DIR", "PREPUSH_BASE")}
    ident = {"GIT_AUTHOR_NAME": "t", "GIT_COMMITTER_NAME": "t"}
    mail = {"GIT_AUTHOR_EMAIL": "noreply@example.com", "GIT_COMMITTER_EMAIL": "noreply@example.com"}
    return out | ident | mail | {
        "PATH": f"{stub}{os.pathsep}{os.environ['PATH']}", "DL": str(stub / "dl"),
        "DL_PYTHON": sys.executable,
    }


@pytest.fixture
def repo(tmp_path: Path, env: dict[str, str]) -> Path:
    git(tmp_path, "init", "-q", "--bare", "remote.git")
    work = tmp_path / "work"
    work.mkdir()
    git(work, "init", "-q", "-b", "trunk")
    git(work, "remote", "add", "origin", str(tmp_path / "remote.git"))
    (work / "run.sh").write_text('echo "$*" >> runs.log\nexec sh -c "$*"\n')
    (work / "dev-loop.yaml").write_text(CONFIG % "true")
    (work / ".githooks").mkdir()
    (work / ".githooks" / "pre-push").write_text("#!/usr/bin/env sh\nexec dl githook pre-push\n")
    (work / ".githooks" / "pre-push").chmod(0o755)
    git(work, "add", "-A")
    git(work, "commit", "-qm", "base", env=env)
    assert git(work, "push", "-q", "origin", "trunk", env=env).returncode == 0
    git(work, "config", "core.hooksPath", ".githooks")  # on after the base is published
    git(work, "checkout", "-qb", "feature")
    (work / "src").mkdir()
    (work / "src" / "a.py").write_text("a = 1\n")
    return work


def push(work: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    git(work, "add", "-A")
    git(work, "commit", "-qm", "change", env=env)
    return git(work, "push", "origin", "feature", env=env)


def test_passing_steps_let_the_push_through(repo: Path, env: dict[str, str]) -> None:
    done = push(repo, env)
    assert done.returncode == 0, done.stderr
    assert "PASS lint" in done.stdout + done.stderr
    assert "SKIP web" in done.stdout + done.stderr  # its side's files were not touched
    assert (repo / "runs.log").read_text() == "true\n"  # through verify.runner
    ci = (repo.parent / "ci.log").read_text().splitlines()
    assert ci == [f"ci {name} --base origin/trunk" for name in
                  ("size-cap", "records-current", "rules-current", "unparsed")]
    assert git(repo, "ls-remote", "origin", "feature").stdout


def test_the_hook_judges_from_a_named_base(repo: Path, env: dict[str, str]) -> None:
    git(repo, "update-ref", "refs/remotes/origin/parent", "trunk")  # the stacked part's base
    done = push(repo, env | {"PREPUSH_BASE": "origin/parent"})
    assert done.returncode == 0, done.stderr
    ci = (repo.parent / "ci.log").read_text().splitlines()
    assert ci == [f"ci {name} --base origin/parent" for name in
                  ("size-cap", "records-current", "rules-current", "unparsed")]


@pytest.mark.parametrize(("on_remote", "judged"), [(True, "parent"), (False, "trunk")])
def test_the_hook_judges_from_the_remembered_parent_while_the_remote_has_it(
    repo: Path, env: dict[str, str], on_remote: bool, judged: str,  # noqa: FBT001
) -> None:
    git(repo, "update-ref", "refs/remotes/origin/parent", "trunk")  # `dl pr --base parent`
    git(repo, "config", "branch.feature.dl-base", "parent")
    if on_remote:  # else merged and deleted, the tracking ref left stale
        git(repo.parent / "remote.git", "branch", "parent", "trunk")
    done = push(repo, env)
    assert done.returncode == 0, done.stderr
    ci = (repo.parent / "ci.log").read_text().splitlines()
    assert ci == [f"ci {name} --base origin/{judged}" for name in
                  ("size-cap", "records-current", "rules-current", "unparsed")]


def test_a_failing_step_blocks_the_push(repo: Path, env: dict[str, str]) -> None:
    (repo / "dev-loop.yaml").write_text(CONFIG % "false")
    done = push(repo, env)
    assert done.returncode != 0
    assert "FAIL lint" in done.stdout + done.stderr
    assert not git(repo, "ls-remote", "origin", "feature").stdout


def test_a_touched_side_runs_and_an_unknown_step_fails(repo: Path, env: dict[str, str]) -> None:
    (repo / "web").mkdir()
    (repo / "web" / "x.ts").write_text("x\n")
    (repo / "dev-loop.yaml").write_text((CONFIG % "true").replace("[lint, web]", "[lint, web, nope]"))
    done = push(repo, env)
    assert done.returncode != 0
    assert "FAIL web" in done.stdout + done.stderr
    assert "FAIL nope" in done.stdout + done.stderr  # a step with no command is not run, never clear


def test_pre_commit_warns_on_shared_settings_and_never_blocks(repo: Path, env: dict[str, str]) -> None:
    (repo / ".claude").mkdir()
    (repo / ".claude" / "settings.json").write_text("{}")
    git(repo, "add", "-A")
    done = subprocess.run(["bash", str(DL), "githook", "pre-commit"], cwd=repo, env=env,
                          capture_output=True, text=True, check=False)
    assert done.returncode == 0
    assert ".claude/settings.json is staged" in done.stderr


def test_an_unknown_hook_is_refused(repo: Path, env: dict[str, str]) -> None:
    done = subprocess.run(["bash", str(DL), "githook", "post-merge"], cwd=repo, env=env,
                          capture_output=True, text=True, check=False)
    assert done.returncode == 2


def test_written_files_are_neutral_and_never_skip_the_hook() -> None:
    assert check(WRITTEN) == []
    root = PLUGIN.parents[1]
    assert not [p for p in WRITTEN if "--no-" + "verify" in (root / p).read_text()]


def test_a_step_with_the_affected_placeholder_runs_in_full(repo: Path, env: dict[str, str]) -> None:
    config = (CONFIG % "test -z {affected}").replace('web: "false"', 'web: "true"')
    (repo / "dev-loop.yaml").write_text(config)  # a changed config runs every side
    done = push(repo, env)
    assert done.returncode == 0, done.stdout + done.stderr
    assert (repo / "runs.log").read_text() == "test -z\ntrue\n"  # the placeholder is gone


def test_a_host_step_skips_the_runner(repo: Path, env: dict[str, str]) -> None:
    config = CONFIG.replace("prepush: [lint, web]", "prepush: [lint, web]\n  host_steps: [lint]")
    (repo / "dev-loop.yaml").write_text(config % "true")
    git(repo, "commit", "-qam", "host steps", env=env)
    git(repo, "update-ref", "refs/remotes/origin/trunk", "HEAD")  # the config is in the base
    done = push(repo, env)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "PASS lint" in done.stdout + done.stderr
    assert not (repo / "runs.log").exists()  # lint ran bare; web is skipped, none through the runner
