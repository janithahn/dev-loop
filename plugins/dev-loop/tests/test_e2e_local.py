"""The whole flow through `bin/dl` on a local-hosted temp repo: propose → pr → threads → land.

No hosted pull-request system and no network: the remote is a local bare repository, the host is
`host.provider: local`, and every host call goes through `dl host`. Land is also shown refused on a
failing gate.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from test_e2e import DL, approve, dl, git
from test_neutral_guards import GREEN


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    (tmp_path / "gitconfig").write_text("", encoding="utf-8")
    drop = ("CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_ROOT", "CLAUDE_PLUGIN_DATA", "GIT_DIR")
    base = {k: v for k, v in os.environ.items() if k not in drop and not k.startswith("GIT_CONFIG")}
    return base | {
        "GIT_CONFIG_GLOBAL": str(tmp_path / "gitconfig"), "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0", "GIT_AUTHOR_NAME": "t", "GIT_COMMITTER_NAME": "t",
        "GIT_AUTHOR_EMAIL": "noreply@example.com", "GIT_COMMITTER_EMAIL": "noreply@example.com",
        "DL_PYTHON": sys.executable, "HOME": str(tmp_path)}  # HOME holds the lander's lock


@pytest.fixture
def work(tmp_path: Path, env: dict[str, str]) -> Path:
    bare, work = tmp_path / "origin.git", tmp_path / "work"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare), env=env)
    git(tmp_path, "clone", "-q", str(bare), str(work), env=env)
    (work / "README.md").write_text("# app\n", encoding="utf-8")
    git(work, "add", "-A", env=env)
    git(work, "commit", "-qm", "base", env=env)
    git(work, "push", "-q", "origin", "HEAD:main", env=env)
    git(work, "remote", "set-head", "origin", "main", env=env)
    return work


def host(cwd: Path, env: dict[str, str], verb: str, body: dict) -> dict:
    done = subprocess.run(["bash", str(DL), "host", verb, "--json"], cwd=cwd, check=False,
                          input=json.dumps(body), capture_output=True, text=True,
                          env={**env, "CLAUDE_PROJECT_DIR": str(cwd)})
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _ready(work: Path, env: dict[str, str], gate: str) -> tuple[Path, str, str]:
    """init (local), propose, build, verify, pr; return (worktree, branch, pr id)."""
    assert dl(work, env, "init", "--host", "local").returncode == 0
    config = yaml.safe_load((work / "dev-loop.yaml").read_text(encoding="utf-8"))
    assert config["host"]["provider"] == "local"
    assert not (work / "azure").exists() and not (work / ".github").exists()
    git(work, "config", "--unset", "core.hooksPath", env=env)  # the githooks have their own tests
    with (work / "dev-loop.yaml").open("a", encoding="utf-8") as file:
        file.write(f"verify:\n  steps: {{gate: '{gate}'}}\n")
    git(work, "add", "-A", env=env)
    git(work, "commit", "-qm", "dev loop", env=env)
    git(work, "push", "-q", "origin", "HEAD:main", env=env)

    proposed = dl(work, env, "propose", "add-search", "--title", "Add search")
    assert proposed.returncode == 0, proposed.stderr
    tree = Path(proposed.stdout.strip())
    approve(tree, env, "add-search")
    branch = f"{config['branches']['change_prefix']}add-search"
    assert git(tree, "branch", "--show-current", env=env) == branch
    spec = tree / ".dev-loop" / "changes" / "add-search" / "spec.md"
    spec.write_text("## Acceptance criteria\n- `dl x` prints y\n", encoding="utf-8")
    (tree / "src.py").write_text("x = 1\n", encoding="utf-8")
    simplified = dl(tree, env, "record", "add-search", "simplifier", "edits=none")
    assert simplified.returncode == 0, simplified.stdout + simplified.stderr
    git(tree, "add", "-A", env=env)
    git(tree, "commit", "-qm", "add search", env=env)

    opened = dl(tree, env, "pr", "add-search")
    assert opened.returncode == 0, opened.stdout + opened.stderr
    pr = host(tree, env, "pr-for-branch", {"branch": branch})["id"]
    assert opened.stdout.strip().endswith(pr)
    return tree, branch, pr


def _finish(tree: Path, env: dict[str, str], pr: str) -> None:
    """Tick the tasks and write the done rule's records through `dl record` (land judges them)."""
    folder = tree / ".dev-loop" / "changes" / "add-search"
    (folder / "tasks.md").write_text("- [x] T1\n", encoding="utf-8")
    (folder / "verify-report.md").write_text("# verify\n", encoding="utf-8")
    host(tree, env, "review-run", {"pr": pr, "head": git(tree, "rev-parse", "HEAD", env=env),
                                   "base": "full", "rc": 0})  # V23: a round needs a clean run
    for item in GREEN:
        fields = [f"{key}={pr if key == 'pr' else value}" for key, value in item.items()
                  if key != "kind"]
        done = dl(tree, env, "record", "add-search", str(item["kind"]), *fields)
        assert done.returncode == 0, done.stdout + done.stderr
    git(tree, "add", "-A", env=env)
    git(tree, "commit", "-qm", "records", env=env)


def test_the_whole_local_flow(work: Path, env: dict[str, str]) -> None:
    tree, branch, pr = _ready(work, env, "true")
    # the branch reached the bare remote
    assert git(work.parent / "origin.git", "rev-parse", branch, env=env) == git(
        tree, "rev-parse", "HEAD", env=env)

    # doctor: the host row is green when the remote is reachable with plain git
    doctor = dl(tree, env, "doctor")
    assert "host reachable" in doctor.stdout
    assert "git only; none needed" in doctor.stdout

    # a review thread, resolved, then the clean round recorded
    tid = host(tree, env, "thread-post",
               {"pr": pr, "file": "/src.py", "line": 1, "text": "**major** — x\n\n`crid:z1`"})["id"]
    assert host(tree, env, "threads", {"pr": pr})[0]["comments"][0]["content"].endswith("`crid:z1`")
    host(tree, env, "thread-status", {"pr": pr, "thread": tid, "status": "fixed"})
    head = git(tree, "rev-parse", "HEAD", env=env)
    host(tree, env, "review-run", {"pr": pr, "head": head, "base": "full", "rc": 0})
    host(tree, env, "set-status",
         {"pr": pr, "sha": head, "state": "success", "description": "round 1: 0b 0M"})

    # land: refused until the change meets the done rule, then fast-forward main on the
    # remote to the branch head and delete the source branch
    refused = dl(tree, env, "land", pr)
    assert refused.returncode == 1 and "not done: add-search: tasks open" in refused.stdout
    _finish(tree, env, pr)
    head = git(tree, "rev-parse", "HEAD", env=env)
    landed = dl(tree, env, "land", pr)
    assert landed.returncode == 0, landed.stdout + landed.stderr
    assert git(work.parent / "origin.git", "rev-parse", "main", env=env) == head
    refs = git(work.parent / "origin.git", "for-each-ref", "--format=%(refname)",
               "refs/heads", env=env)
    assert branch not in refs


def test_land_is_refused_on_a_failing_gate(work: Path, env: dict[str, str]) -> None:
    tree, branch, pr = _ready(work, env, "false")
    tid = host(tree, env, "thread-post",
               {"pr": pr, "file": "/src.py", "line": 1, "text": "**m** — x\n\n`crid:z1`"})["id"]
    host(tree, env, "thread-status", {"pr": pr, "thread": tid, "status": "fixed"})
    head = git(tree, "rev-parse", "HEAD", env=env)
    host(tree, env, "review-run", {"pr": pr, "head": head, "base": "full", "rc": 0})
    host(tree, env, "set-status",
         {"pr": pr, "sha": head, "state": "success", "description": "round 1"})
    _finish(tree, env, pr)
    before = git(work.parent / "origin.git", "rev-parse", "main", env=env)
    landed = dl(tree, env, "land", pr)
    assert landed.returncode == 1
    assert "gate failed" in landed.stdout
    assert git(work.parent / "origin.git", "rev-parse", "main", env=env) == before  # not merged


def test_the_records_travel_with_the_branch(work: Path, env: dict[str, str]) -> None:
    tree, branch, pr = _ready(work, env, "true")
    tid = host(tree, env, "thread-post",
               {"pr": pr, "file": "/src.py", "line": 1, "text": "**major** — x\n\n`crid:z1`"})["id"]
    host(tree, env, "thread-status", {"pr": pr, "thread": tid, "status": "fixed"})
    head = git(tree, "rev-parse", "HEAD", env=env)
    host(tree, env, "review-run", {"pr": pr, "head": head, "base": "full", "rc": 0})
    host(tree, env, "set-status",
         {"pr": pr, "sha": head, "state": "success", "description": "round 1: 0b 0M"})
    assert git(tree, "status", "--porcelain", env=env) == ""
    subjects = git(tree, "log", "-5", "--format=%s", env=env).splitlines()[::-1]
    assert [s.split()[1] for s in subjects] == ["pr", "review-thread", "review-thread",
                                                 "review-run", "review-clean"]

    # another checkout that fetches the branch finds the pr by id
    git(tree, "push", "-q", "origin", branch, env=env)
    other = work.parent / "other"
    git(work.parent, "clone", "-q", "-b", branch, str(work.parent / "origin.git"), str(other),
        env=env)
    assert host(other, env, "pr-for-branch", {"branch": branch})["id"] == pr
    assert host(other, env, "threads", {"pr": pr})[0]["status"] == "fixed"

    # the landed commit carries the pr and review records
    _finish(tree, env, pr)
    assert dl(tree, env, "land", pr).returncode == 0
    for name in ("host.jsonl", "review-threads.jsonl"):
        git(work.parent / "origin.git", "cat-file", "-e",
            f"main:.dev-loop/changes/add-search/{name}", env=env)
