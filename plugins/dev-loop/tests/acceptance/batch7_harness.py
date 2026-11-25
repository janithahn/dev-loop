"""Batch 7's harness: the isolated environment, the hub fixtures and the dl/host/git calls."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from test_e2e import approve

PLUGIN = Path(__file__).resolve().parents[2]
DL = PLUGIN / "bin" / "dl"
STUB = """#!/usr/bin/env bash
# json: a clean review verdict on every file of the diff on stdin (the auth probe reads is_error)
json() {
  jq -cn --argjson n "$(grep -c '^diff --git')" '{is_error: false, result: ({verdict: "APPROVE",
    summary: "ok", files_seen: $n, findings: [],
    spec: [{id: "S1", status: "met", evidence: "ok"}]} | tojson)}'; exit 0
}
echo "$@" >> "$STUB_LOG"
case "${STUB_MODE:-ok}" in sleep) sleep 600;; exit1) exit 1;; empty) exit 0;; json) json;; esac
echo ok
"""
CHANGE = ".dev-loop/changes/demo"
DONE = [["round", "pr=1", "round=1", "base=abc", "blocker=0", "major=0", "minor=0", "fixed=0",
         "deferred=0", "active=0", "turns=1", "seconds=1", "tokens_in=1", "tokens_out=1",
         "unmetered=0"],
        ["metrics", "pr=1", "change=demo", "rounds=1", "per_round=0/0/0", "total=0/0/0",
         "lessons=0"],
        ["lessons-logged", "count=0"], ["verify", "result=green", "head=abc"],
        ["simplifier", "edits=none"], ["skills", "skills=tdd"]]


def run(cwd: Path, env: dict[str, str], *cmd: str, stdin: str = "",
        timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(cmd), cwd=cwd, env=env, capture_output=True, text=True,
                          check=False, timeout=timeout, input=stdin)


def git(cwd: Path, env: dict[str, str], *args: str) -> str:
    done = run(cwd, env, "git", *args)
    assert done.returncode == 0, (args, done.stderr)
    return done.stdout.strip()


def dl(cwd: Path, env: dict[str, str], *args: str, stdin: str = "",
       extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    full = {**env, "CLAUDE_PROJECT_DIR": str(cwd), **(extra or {})}
    return run(cwd, full, "bash", str(DL), *args, stdin=stdin)


def host(cwd: Path, env: dict[str, str], verb: str, body: dict[str, Any]) -> Any:
    done = dl(cwd, env, "host", verb, "--json", stdin=json.dumps(body))
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    """The harness preamble: every per-user path under tmp_path, the fake claude on PATH."""
    home, stub = tmp_path / "home", tmp_path / "stub"
    for folder in (home / ".claude", tmp_path / "data", stub):
        folder.mkdir(parents=True)
    (stub / "claude").write_text(STUB, encoding="utf-8")
    (stub / "claude").chmod(0o755)
    (tmp_path / "gitconfig").write_text(
        "[user]\n\temail = noreply@example.invalid\n\tname = tester\n[init]\n\tdefaultBranch = main\n",
        encoding="utf-8")
    drop = ("CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_ROOT", "GIT_DIR", "GIT_WORK_TREE")
    base = {k: v for k, v in os.environ.items() if k not in drop and not k.startswith("GIT_")}
    return base | {
        "HOME": str(home), "CLAUDE_CONFIG_DIR": str(home / ".claude"),
        "CLAUDE_PLUGIN_DATA": str(tmp_path / "data"),
        "GIT_CONFIG_GLOBAL": str(tmp_path / "gitconfig"),
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0", "DL_PYTHON": sys.executable,
        "STUB_LOG": str(tmp_path / "claude.log"), "PATH": f"{stub}{os.pathsep}{base['PATH']}"}


def make_local(tmp_path: Path, env: dict[str, str], remote: str = "origin") -> Path:
    """F-HUB (F-HUB2 when remote is hub), then F-LOCAL: init --host local, verify unit true."""
    bare, work = tmp_path / "hub.git", tmp_path / "work"
    git(tmp_path, env, "init", "-q", "--bare", "-b", "main", str(bare))
    git(tmp_path, env, "clone", "-q", "-o", remote, str(bare), str(work))
    (work / "README.md").write_text("# app\n", encoding="utf-8")
    git(work, env, "add", "-A")
    git(work, env, "commit", "-qm", "base")
    git(work, env, "push", "-q", remote, "HEAD:main")
    git(work, env, "remote", "set-head", remote, "main")
    done = dl(work, env, "init", "--host", "local")
    assert done.returncode == 0, done.stdout + done.stderr
    git(work, env, "config", "--unset", "core.hooksPath")  # the githooks have their own tests
    edit_config(work, lambda c: c.update(verify={"steps": {"unit": "true"}}))
    edit_config(work, lambda c: c["branches"].update(remote=remote))
    git(work, env, "add", "-A")
    git(work, env, "commit", "-qm", "dev loop")
    git(work, env, "push", "-q", remote, "HEAD:main")
    return work


def edit_config(work: Path, change: object) -> None:
    path = work / "dev-loop.yaml"
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    loaded.setdefault("branches", {})
    change(loaded)  # type: ignore[operator]
    path.write_text(yaml.safe_dump(loaded), encoding="utf-8")


def make_chg(work: Path, env: dict[str, str]) -> Path:
    """F-CHG: `dl propose demo`, a file added, every task ticked, committed; the worktree."""
    done = dl(work, env, "propose", "demo", "--title", "Demo")
    assert done.returncode == 0, done.stdout + done.stderr
    tree = Path(done.stdout.strip())
    approve(tree, env, "demo")
    (tree / "app.py").write_text("x = 1\n", encoding="utf-8")
    (tree / CHANGE / "tasks.md").write_text("- [x] T1: app\n", encoding="utf-8")
    git(tree, env, "add", "-A")
    git(tree, env, "commit", "-qm", "build demo")
    return tree


def open_pr(tree: Path, env: dict[str, str]) -> str:
    simplified = dl(tree, env, "record", "demo", "simplifier", "edits=none")  # dl pr asks it
    assert simplified.returncode == 0, simplified.stdout + simplified.stderr
    git(tree, env, "add", "-A")
    git(tree, env, "commit", "-qm", "simplifier record")
    done = dl(tree, env, "pr", "demo")
    assert done.returncode == 0, done.stdout + done.stderr
    branch = git(tree, env, "branch", "--show-current")
    return str(host(tree, env, "pr-for-branch", {"branch": branch})["id"])


def clean(tree: Path, env: dict[str, str], pr: str, state: str = "success") -> None:
    sha = git(tree, env, "rev-parse", "HEAD")
    host(tree, env, "review-run", {"pr": pr, "head": sha, "base": "full", "rc": 0})
    host(tree, env, "set-status", {"pr": pr, "sha": sha, "state": state, "description": "r1"})


def finish(tree: Path, env: dict[str, str], rows: list[list[str]] = DONE) -> None:
    """Write the done rule's records through `dl record`, verify-report.md, and commit them."""
    (tree / CHANGE / "verify-report.md").write_text("# verify\n", encoding="utf-8")
    pr = str(host(tree, env, "pr-for-branch", {"branch": git(tree, env, "branch",
                                                              "--show-current")})["id"])
    host(tree, env, "review-run", {"pr": pr, "head": git(tree, env, "rev-parse", "HEAD"),
                                   "base": "full", "rc": 0})  # V23: a round needs a clean run
    for row in rows:
        done = dl(tree, env, "record", "demo", *[f"pr={pr}" if f == "pr=1" else f for f in row])
        assert done.returncode == 0, done.stdout + done.stderr
    git(tree, env, "add", "-A")
    git(tree, env, "commit", "-qm", "records")


def hub_main(env: dict[str, str]) -> str:
    """The hub's main (the hub sits beside the isolated HOME)."""
    hub = Path(env["HOME"]).parent / "hub.git"
    return git(hub, env, "rev-parse", "main")


def ready(tree: Path, env: dict[str, str]) -> str:
    """A PR with a clean round and every done record: today's land would take it."""
    pr = open_pr(tree, env)
    finish(tree, env)
    clean(tree, env, pr)
    return pr


def stopped(done: subprocess.CompletedProcess[str], *needles: str) -> bool:
    lines = (done.stdout + done.stderr).strip().splitlines()
    return done.returncode == 1 and len(lines) == 1 and all(n in lines[0] for n in needles)
