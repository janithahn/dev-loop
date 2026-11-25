"""`dl host set-status`: a hosted PR's clean status follows a records-only push (v0.4.11 F3)."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from dev_loop.hosts import cli

RECORDS = ".dev-loop/changes/c/records.jsonl"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=noreply@example.com",
                           "-C", str(repo), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


def commit(repo: Path, path: str, text: str) -> str:
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text(text, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", path)
    return git(repo, "rev-parse", "HEAD")


def status(repo: Path, monkeypatch: pytest.MonkeyPatch, sha: str) -> list[Any]:
    calls: list[Any] = []
    stub = SimpleNamespace(set_status=lambda *args: calls.append(args))
    monkeypatch.setattr(cli, "adapter", lambda: stub)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
    body = {"sha": sha, "state": "success", "description": "round 1: 0 blockers", "pr": 7}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(body)))
    assert cli.main(["set-status", "--json"]) == 0
    return calls


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q")
    (tmp_path / "dev-loop.yaml").write_text("version: 1\n", encoding="utf-8")
    return tmp_path


def test_set_status_follows_a_records_only_commit(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reviewed = commit(repo, "src/a.py", "x = 1\n")
    head = commit(repo, RECORDS, "{}\n")
    [(sha, state, said, pr)] = status(repo, monkeypatch, reviewed)
    assert (sha, state, pr) == (head, "success", 7)
    assert said.endswith(f"(reviewed {reviewed[:12]}; records only since)")


def test_set_status_stays_on_the_reviewed_sha_after_a_source_commit(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reviewed = commit(repo, "src/a.py", "x = 1\n")
    commit(repo, "src/a.py", "x = 2\n")
    [(sha, _, said, _)] = status(repo, monkeypatch, reviewed)
    assert (sha, said) == (reviewed, "round 1: 0 blockers")
