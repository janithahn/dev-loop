"""The harness of acceptance batch 3: an isolated HOME, plugin data dir and global git config,
a local bare repository as the hub (F-HUB), and F-LOCAL on it; every command has a timeout."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

PLUGIN = Path(__file__).resolve().parents[2]
DL = PLUGIN / "bin" / "dl"
DROP = ("CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_ROOT", "PREPUSH_BASE", "DL", "GIT_DIR")
SECRET = "PLANTED-BATCH3-SECRET-41"
HANG = "sleep 100000"


@dataclass(frozen=True)
class Done:
    code: int | None  # None: killed at the timeout
    out: str
    seconds: float


def run(args: list[str], cwd: Path, env: dict[str, str], timeout: float = 120) -> Done:
    """Run with stdout+stderr merged; past the timeout kill the whole process group."""
    start = time.monotonic()
    with subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.DEVNULL, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          start_new_session=True) as proc:
        try:
            out, _ = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            out, _ = proc.communicate()
            return Done(None, out, time.monotonic() - start)
    return Done(proc.returncode, out, time.monotonic() - start)


def dl(cwd: Path, env: dict[str, str], *args: str, timeout: float = 120) -> Done:
    return run(["bash", str(DL), *args], cwd, env, timeout)


def git(cwd: Path, env: dict[str, str], *args: str) -> str:
    done = run(["git", *args], cwd, env, 60)
    assert done.code == 0, done.out
    return done.out.strip()


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    """The harness: HOME, config dir, plugin data and global git config all under tmp_path."""
    home, tools = tmp_path / "home", tmp_path / "bin"
    for folder in (home / ".claude", tmp_path / "data", tools):
        folder.mkdir(parents=True)
    (tools / "dl").write_text(f'#!/bin/sh\nexec bash "{DL}" "$@"\n', encoding="utf-8")
    (tools / "dl").chmod(0o755)
    base = {k: v for k, v in os.environ.items() if k not in DROP and not k.startswith("GIT_")}
    out = base | {
        "HOME": str(home), "CLAUDE_CONFIG_DIR": str(home / ".claude"),
        "CLAUDE_PLUGIN_DATA": str(tmp_path / "data"), "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": str(tmp_path / "gitconfig"), "GIT_TERMINAL_PROMPT": "0",
        "DL_PYTHON": sys.executable, "PATH": f"{tools}{os.pathsep}{os.environ['PATH']}"}
    for key, value in (("user.email", "noreply@example.invalid"), ("user.name", "tester"),
                       ("init.defaultBranch", "main")):
        git(tmp_path, out, "config", "--global", key, value)
    return out


def f_hub(tmp_path: Path, env: dict[str, str]) -> Path:
    """F-HUB: a bare hub, cloned, one commit pushed."""
    git(tmp_path, env, "init", "-q", "--bare", "hub.git")
    git(tmp_path, env, "clone", "-q", "hub.git", "work")
    work = tmp_path / "work"
    (work / "README.md").write_text("# app\n", encoding="utf-8")
    git(work, env, "add", "-A")
    git(work, env, "commit", "-qm", "base")
    git(work, env, "push", "-q", "origin", "main")
    return work


def edit(work: Path, change: dict[str, Any]) -> None:
    """Merge `change` into dev-loop.yaml, one level deep per section."""
    path = work / "dev-loop.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    for key, value in change.items():
        doc[key] = {**doc.get(key, {}), **value} if isinstance(value, dict) else value
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")


@pytest.fixture
def local(tmp_path: Path, env: dict[str, str]) -> Path:
    """F-LOCAL: F-HUB, `dl init --host local`, verify.steps {unit: "true"}, published.

    The disk threshold is raised to 99% so the host machine's own disk never reddens a row.
    """
    work = f_hub(tmp_path, env)
    assert dl(work, env, "init", "--host", "local").code == 0
    edit(work, {"verify": {"steps": {"unit": "true"}}, "cleanup": {"threshold_pct": 99}})
    git(work, env, "config", "--unset", "core.hooksPath")  # main is protected by pre-commit
    git(work, env, "add", "-A")
    git(work, env, "commit", "-qm", "dev loop")
    git(work, env, "push", "-q", "origin", "main")
    git(work, env, "config", "core.hooksPath", ".githooks")
    return work


def reds(work: Path, env: dict[str, str]) -> dict[str, str]:
    """The red rows of `dl doctor --json`, name to fix."""
    done = dl(work, env, "doctor", "--json")
    assert done.code is not None, done.out
    return {row["name"]: row["fix"] for row in json.loads(done.out) if row["status"] == "red"}


def path_without(tool: str, tmp_path: Path) -> str:
    """A PATH holding a link to every executable on today's PATH except `tool`."""
    shadow = tmp_path / f"no-{tool}"
    shadow.mkdir()
    for folder in os.environ["PATH"].split(os.pathsep):
        for entry in Path(folder).glob("*") if Path(folder).is_dir() else []:
            link = shadow / entry.name
            if entry.name != tool and not link.exists() and os.access(entry, os.X_OK):
                link.symlink_to(entry)
    return f"{tmp_path / 'bin'}{os.pathsep}{shadow}"
