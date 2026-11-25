"""The githooks `dl init` writes find `dl` outside Claude Code: PATH first, else the newest copy in
the plugin cache; with neither, the commit goes through and stderr says the checks were NOT run."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from dev_loop import init
from test_neutral import check

MAIL = "noreply@example.com"


def git(root: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, env=env,
                          check=False)


def fake_dl(folder: Path, log: Path, code: int) -> None:
    """A `dl` that logs where it ran from and exits with code."""
    folder.mkdir(parents=True)
    (folder / "dl").write_text(f'#!/bin/sh\necho "{folder} $*" >> "{log}"\nexit {code}\n')
    (folder / "dl").chmod(0o755)


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    """PATH without any `dl`, an empty config dir, a commit identity."""
    path = os.pathsep.join(
        part for part in os.environ["PATH"].split(os.pathsep) if not (Path(part) / "dl").exists()
    )
    out = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    return out | {
        "PATH": path, "CLAUDE_CONFIG_DIR": str(tmp_path / "config"),
        "GIT_AUTHOR_NAME": "t", "GIT_COMMITTER_NAME": "t",
        "GIT_AUTHOR_EMAIL": MAIL, "GIT_COMMITTER_EMAIL": MAIL,
    }


@pytest.fixture
def repo(tmp_path: Path, env: dict[str, str]) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    git(work, env, "init", "-q")
    init.init(work)
    git(work, env, "add", "-A")
    return work


def cache(env: dict[str, str], version: str) -> Path:
    return Path(env["CLAUDE_CONFIG_DIR"]) / "plugins/cache/dev-loop/dev-loop" / version / "bin"


def test_no_dl_commit_goes_through_and_says_not_run(repo: Path, env: dict[str, str]) -> None:
    done = git(repo, env, "commit", "-qm", "base")
    assert done.returncode == 0, done.stderr
    assert "dev-loop: dl not found, pre-commit checks NOT run" in done.stderr


def test_newest_cached_dl_runs_and_its_failure_blocks(
    repo: Path, env: dict[str, str], tmp_path: Path
) -> None:
    log = tmp_path / "dl.log"
    fake_dl(cache(env, "0.1.9"), log, 0)
    fake_dl(cache(env, "0.1.10"), log, 3)
    done = git(repo, env, "commit", "-qm", "base")
    assert done.returncode != 0
    assert log.read_text() == f"{cache(env, '0.1.10')} githook pre-commit\n"
    assert git(repo, env, "rev-parse", "-q", "--verify", "HEAD").returncode != 0


def test_dl_on_path_wins(repo: Path, env: dict[str, str], tmp_path: Path) -> None:
    log = tmp_path / "dl.log"
    fake_dl(cache(env, "9.9.9"), log, 3)
    fake_dl(tmp_path / "onpath", log, 0)
    env = env | {"PATH": f"{tmp_path / 'onpath'}{os.pathsep}{env['PATH']}"}
    done = git(repo, env, "commit", "-qm", "base")
    assert done.returncode == 0, done.stderr
    assert log.read_text() == f"{tmp_path / 'onpath'} githook pre-commit\n"


def test_init_again_after_commit_changes_nothing(repo: Path, env: dict[str, str]) -> None:
    assert git(repo, env, "commit", "-qm", "base").returncode == 0
    assert init.init(repo) == []
    assert git(repo, env, "status", "--short").stdout == ""


def test_hooks_are_posix_sh_and_neutral(repo: Path) -> None:
    for name in init.HOOKS:
        hook = repo / ".githooks" / name
        assert os.access(hook, os.X_OK)
        assert subprocess.run(["sh", "-n", str(hook)], check=False).returncode == 0
        assert "NOT run" in hook.read_text()
    assert check([__file__]) == []
