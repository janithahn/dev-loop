"""Acceptance batch 4 (lifecycle commands): the plan's scriptable rows, run through `bin/dl`.

Each test isolates HOME, CLAUDE_CONFIG_DIR, CLAUDE_PLUGIN_DATA and the git config under its own
temporary folder; a fake `claude` first on PATH logs any call. The remote is a local bare repo.
A row that fails on today's code is a strict xfail naming its symptom, so a fix flips it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_e2e import approve

PLUGIN = Path(__file__).resolve().parents[2]
DL = PLUGIN / "bin" / "dl"
STUB = """#!/usr/bin/env bash
echo "$@" >> "$STUB_LOG"
case "${STUB_MODE:-ok}" in sleep) sleep 600;; exit1) exit 1;; empty) exit 0;; esac
echo ok
"""


def run(cwd: Path, env: dict[str, str], *cmd: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(cmd), cwd=cwd, env=env, capture_output=True, text=True,
                          check=False, timeout=120)


def git(cwd: Path, env: dict[str, str], *args: str) -> str:
    done = run(cwd, env, "git", *args)
    assert done.returncode == 0, (args, done.stderr)
    return done.stdout.strip()


def dl(cwd: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return run(cwd, {**env, "CLAUDE_PROJECT_DIR": str(cwd)}, "bash", str(DL), *args)


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


@pytest.fixture
def hub(tmp_path: Path, env: dict[str, str]) -> Path:
    """F-HUB: a bare hub, cloned to `work`, one commit pushed."""
    bare, work = tmp_path / "hub.git", tmp_path / "work"
    git(tmp_path, env, "init", "-q", "--bare", "-b", "main", str(bare))
    git(tmp_path, env, "clone", "-q", str(bare), str(work))
    (work / "README.md").write_text("# app\n", encoding="utf-8")
    git(work, env, "add", "-A")
    git(work, env, "commit", "-qm", "base")
    git(work, env, "push", "-q", "origin", "HEAD:main")
    git(work, env, "remote", "set-head", "origin", "main")
    return work


@pytest.fixture
def local(hub: Path, env: dict[str, str]) -> Path:
    """F-LOCAL: `dl init --host local` with verify.steps {unit: "true"}, pushed."""
    done = dl(hub, env, "init", "--host", "local")
    assert done.returncode == 0, done.stdout + done.stderr
    git(hub, env, "config", "--unset", "core.hooksPath")  # the githooks have their own tests
    with (hub / "dev-loop.yaml").open("a", encoding="utf-8") as file:
        file.write("verify:\n  steps: {unit: 'true'}\n")
    git(hub, env, "add", "-A")
    git(hub, env, "commit", "-qm", "dev loop")
    git(hub, env, "push", "-q", "origin", "HEAD:main")
    return hub


def propose(work: Path, env: dict[str, str]) -> Path:
    done = dl(work, env, "propose", "demo", "--title", "Demo")
    assert done.returncode == 0, done.stdout + done.stderr
    approve(Path(done.stdout.strip()), env, "demo")
    return Path(done.stdout.strip())


@pytest.fixture
def chg(local: Path, env: dict[str, str]) -> Path:
    """F-CHG: `dl propose demo`, a file added, every task ticked, committed; the worktree."""
    tree = propose(local, env)
    (tree / "app.py").write_text("x = 1\n", encoding="utf-8")
    (folder(tree) / "tasks.md").write_text("- [x] T1: app\n", encoding="utf-8")
    simplified = dl(tree, env, "record", "demo", "simplifier", "edits=none")  # dl pr asks it
    assert simplified.returncode == 0, simplified.stdout + simplified.stderr
    git(tree, env, "add", "-A")
    git(tree, env, "commit", "-qm", "build demo")
    return tree


def folder(tree: Path) -> Path:
    return tree / ".dev-loop" / "changes" / "demo"


def state(work: Path, env: dict[str, str]) -> tuple[str, str]:
    """The branches and worktrees a refusal must leave as they were."""
    return git(work, env, "branch", "-a"), git(work, env, "worktree", "list", "--porcelain")


def one_line(done: subprocess.CompletedProcess[str]) -> bool:
    return done.returncode != 0 and len(done.stderr.strip().splitlines()) == 1


def test_l1_propose_branches_off_the_remote_tip_with_templates_filled(
        local: Path, env: dict[str, str]) -> None:
    tip = git(local, env, "rev-parse", "origin/main")
    tree = propose(local, env)
    assert tree.is_dir() and git(tree, env, "rev-parse", "HEAD") == tip
    assert git(tree, env, "branch", "--show-current").endswith("demo")
    files = list(folder(tree).iterdir())
    assert {f.name for f in files} >= {"proposal.md", "status.md"}
    assert all("{{" not in f.read_text(encoding="utf-8") for f in files)


@pytest.mark.parametrize("case", ["bad slug", "existing slug", "remote unreachable"])
def test_l2_propose_refusals_leave_nothing_behind(
        case: str, local: Path, env: dict[str, str]) -> None:
    slug = {"bad slug": "Bad_Slug", "existing slug": "demo", "remote unreachable": "offline"}[case]
    if case == "existing slug":
        propose(local, env)
    if case == "remote unreachable":
        git(local, env, "remote", "set-url", "origin", str(local.parent / "missing.git"))
    before = state(local, env)
    done = dl(local, env, "propose", slug)
    assert one_line(done), done.stderr
    assert state(local, env) == before
    if case != "existing slug":
        assert not (local.parent / f"{local.name}-wt" / slug).exists()


@pytest.mark.parametrize("argv", [["propose", "x", "--help"], ["land", "--help"],
                                  ["land", "1", "--help"], ["review", "--help"]], ids=" ".join)
def test_l3_help_never_acts(argv: list[str], chg: Path, env: dict[str, str]) -> None:
    head, before, records = git(chg, env, "rev-parse", "HEAD"), state(chg, env), folder(chg)
    listing = sorted(p.name for p in records.iterdir())
    done = dl(chg, env, *argv)
    assert done.returncode == 0 and done.stdout.strip(), done.stderr
    assert state(chg, env) == before and git(chg, env, "rev-parse", "HEAD") == head
    assert sorted(p.name for p in records.iterdir()) == listing  # no review round written
    assert not (Path(env["HOME"]) / ".dev-loop" / "land.lock").exists()
    assert not Path(env["STUB_LOG"]).exists()  # no model call
    assert git(chg, env, "status", "--porcelain") == ""


def test_l4_a_fresh_change_is_clean_to_the_record_checks(local: Path, env: dict[str, str]) -> None:
    tree = propose(local, env)
    unparsed = dl(tree, env, "ci", "unparsed")
    assert unparsed.returncode == 0, unparsed.stdout + unparsed.stderr
    done = dl(tree, env, "record", "done")
    assert done.returncode == 0, done.stderr
    assert "no record" not in unparsed.stdout + unparsed.stderr + done.stdout + done.stderr


@pytest.mark.parametrize("case", ["skipped", "absent"])
def test_l6_skipped_and_absent_steps_are_not_run_and_red(
        case: str, chg: Path, env: dict[str, str]) -> None:
    args = ["--skip", "unit"]
    if case == "absent":
        text = (chg / "dev-loop.yaml").read_text(encoding="utf-8")
        (chg / "dev-loop.yaml").write_text(text.replace("{unit: 'true'}", "{}"), encoding="utf-8")
        args = []
    done = dl(chg, env, "verify", "demo", *args)
    assert done.returncode == 1
    assert "verify: red" in done.stdout
    assert case == "absent" or "not run" in done.stdout
    last = (folder(chg) / "records.jsonl").read_text(encoding="utf-8").splitlines()[-1]
    assert json.loads(last) | {"kind": "verify", "result": "red"} == json.loads(last)


def finish_but_verify(tree: Path, env: dict[str, str]) -> None:
    """Every done-rule record but the verify, through `dl record`, then committed."""
    records = [
        ["round", "pr=1", "round=1", "base=abc", "blocker=0", "major=0", "minor=0", "fixed=0",
         "deferred=0", "active=0", "turns=1", "seconds=1", "tokens_in=1", "tokens_out=1",
         "unmetered=0"],
        ["metrics", "pr=1", "change=demo", "rounds=1", "per_round=0/0/0", "total=0/0/0",
         "lessons=0"],
        ["lessons-logged", "count=0"], ["simplifier", "edits=none"], ["skills", "skills=tdd"]]
    done = dl(tree, env, "config", "set", "review.fallback", "subagent")  # V23: no PR here
    assert done.returncode == 0, done.stdout + done.stderr
    for fields in records:
        done = dl(tree, env, "record", "demo", *fields)
        assert done.returncode == 0, done.stdout + done.stderr
    git(tree, env, "add", "-A")
    git(tree, env, "commit", "-qm", "records")


def test_l7_a_green_verify_does_not_outlive_the_code(chg: Path, env: dict[str, str]) -> None:
    text = (chg / "dev-loop.yaml").read_text(encoding="utf-8")
    steps = "{unit: 'test ! -e broken'}"
    (chg / "dev-loop.yaml").write_text(text.replace("{unit: 'true'}", steps), encoding="utf-8")
    finish_but_verify(chg, env)
    assert dl(chg, env, "verify", "demo").returncode == 0
    git(chg, env, "add", "-A")
    git(chg, env, "commit", "-qm", "verify green")
    (chg / "broken").write_text("now the unit step fails\n", encoding="utf-8")
    git(chg, env, "add", "-A")
    git(chg, env, "commit", "-qm", "a failing change")
    assert dl(chg, env, "verify", "demo", "--part", "probe").returncode == 1  # the code is red
    git(chg, env, "checkout", "--", ".")
    git(chg, env, "clean", "-fdq")  # drop the probe's red record: only the old green remains
    record = dl(chg, env, "record", "done")
    rule = dl(chg, env, "ci", "done-rule", "demo")
    assert rule.returncode == 1 and "verify" in rule.stdout, rule.stdout
    assert "verify" in record.stdout


def test_l8_l10_pr_titles_without_the_prefix_and_never_duplicates(
        chg: Path, env: dict[str, str]) -> None:
    assert "# Proposal: Demo" in (folder(chg) / "proposal.md").read_text(encoding="utf-8")
    first, second = dl(chg, env, "pr", "demo"), dl(chg, env, "pr", "demo")
    assert first.returncode == 0 and second.returncode == 0, first.stderr + second.stderr
    pr_id = first.stdout.strip().rsplit(" ", 1)[-1]
    last = second.stdout.strip().splitlines()[-1]
    assert last.startswith("open PR") and last.endswith(pr_id)
    got =subprocess.run(["bash", str(DL), "host", "pr-get", "--json"], cwd=chg, check=False,
                         input=json.dumps({"id": pr_id}), capture_output=True, text=True,
                         env={**env, "CLAUDE_PROJECT_DIR": str(chg)}, timeout=120)
    assert got.returncode == 0, got.stderr
    assert json.loads(got.stdout)["title"] == "Demo"


def test_l9_a_rebase_conflict_pushes_nothing_and_aborts(
        chg: Path, local: Path, env: dict[str, str]) -> None:
    (chg / "README.md").write_text("# change side\n", encoding="utf-8")
    git(chg, env, "commit", "-qam", "change side")
    (local / "README.md").write_text("# main side\n", encoding="utf-8")
    git(local, env, "commit", "-qam", "main side")
    git(local, env, "push", "-q", "origin", "HEAD:main")
    head = git(chg, env, "rev-parse", "HEAD")
    done = dl(chg, env, "pr", "demo")
    assert done.returncode == 1
    bare = local.parent / "hub.git"
    assert "demo" not in git(bare, env, "for-each-ref", "--format=%(refname)", "refs/heads")
    paths = [git(chg, env, "rev-parse", "--git-path", p) for p in ("rebase-merge", "rebase-apply")]
    assert not any((chg / p).exists() for p in paths)
    assert git(chg, env, "rev-parse", "HEAD") == head
    assert one_line(done), done.stderr


def test_l13_no_ref_on_a_hub_remote_is_not_checked(hub: Path, env: dict[str, str]) -> None:
    git(hub, env, "remote", "rename", "origin", "hub")
    (hub / "dev-loop.yaml").write_text("branches:\n  remote: hub\n", encoding="utf-8")
    git(hub, env, "checkout", "-qb", "work")
    git(hub, env, "branch", "-D", "main")
    git(hub, env, "update-ref", "-d", "refs/remotes/hub/main")
    git(hub, env, "branch", "dev-loop/cand")
    done = dl(hub, env, "clean", "--json")
    assert done.returncode == 0, done.stderr
    items = [i for i in json.loads(done.stdout)["items"] if i["target"] == "dev-loop/cand"]
    assert items and not items[0]["remove"]
    assert items[0]["reason"].startswith("not checked") and "hub/main" in items[0]["reason"]


def test_l14_stack_rebase_self_check(env: dict[str, str]) -> None:
    done = run(PLUGIN, env, "bash", str(PLUGIN / "tests" / "test-stack-rebase.sh"))
    assert done.returncode == 0, done.stdout + done.stderr
    assert "stack_rebase: ok" in done.stdout
