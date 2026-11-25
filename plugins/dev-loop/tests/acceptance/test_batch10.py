"""Acceptance batch 10 (hooks and duplicate hooks): the plan's scriptable rows as tests.

Each hook runs as hooks.json registers it, as a subprocess with a timeout, HOME,
CLAUDE_CONFIG_DIR, CLAUDE_PLUGIN_DATA and the git config isolated under the test's own folder.
Some rows are only partly pinned by their named tests; the missing case is
here. A row that fails on today's code is a strict xfail naming its symptom, so a fix flips it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[2]
HOOKS = PLUGIN / "hooks"
sys.path.insert(0, str(PLUGIN / "tests"))

from test_dev_loop_context_gate import at_context, denied, event, gate  # noqa: E402
from test_dev_loop_fixtures import Tree  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is absent")


def run(argv: list[str], cwd: Path, env: dict[str, str],
        stdin: str = "", timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, env=env, input=stdin, capture_output=True,  # noqa: S603
                          text=True, check=False, timeout=timeout)


def git(cwd: Path, env: dict[str, str], *args: str) -> None:
    done = run(["git", *args], cwd, env)
    assert done.returncode == 0, (args, done.stderr)


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    """The harness preamble: every per-user path under tmp_path, no git above the test."""
    home = tmp_path / "home"
    for folder in (home / ".claude", tmp_path / "data"):
        folder.mkdir(parents=True)
    (tmp_path / "gitconfig").write_text(
        "[user]\n\temail = noreply@example.invalid\n\tname = tester\n"
        "[init]\n\tdefaultBranch = main\n",
        encoding="utf-8")
    drop = ("CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_ROOT", "DEV_LOOP_ROLE", "DEV_LOOP_HANDOVER")
    base = {k: v for k, v in os.environ.items() if k not in drop and not k.startswith("GIT_")}
    return base | {
        "HOME": str(home), "CLAUDE_CONFIG_DIR": str(home / ".claude"),
        "CLAUDE_PLUGIN_DATA": str(tmp_path / "data"), "CLAUDE_PLUGIN_ROOT": str(PLUGIN),
        "GIT_CONFIG_GLOBAL": str(tmp_path / "gitconfig"), "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0", "GIT_CEILING_DIRECTORIES": str(tmp_path),
        "DL_PYTHON": sys.executable}


@pytest.fixture
def repo(tmp_path: Path, env: dict[str, str]) -> Path:
    """A clone of a bare hub on main, one commit pushed, origin/main known."""
    bare, work = tmp_path / "hub.git", tmp_path / "work"
    git(tmp_path, env, "init", "-q", "--bare", "-b", "main", str(bare))
    git(tmp_path, env, "clone", "-q", str(bare), str(work))
    (work / "README.md").write_text("# app\n", encoding="utf-8")
    git(work, env, "add", "-A")
    git(work, env, "commit", "-qm", "base")
    git(work, env, "push", "-q", "origin", "HEAD:main")
    env["CLAUDE_PROJECT_DIR"] = str(work)
    return work


def finished_change(root: Path, name: str = "demo") -> Path:
    """A change whose every task is ticked and whose records are all missing."""
    folder = root / ".dev-loop" / "changes" / name
    folder.mkdir(parents=True)
    (folder / "tasks.md").write_text("- [x] T1 done\n", encoding="utf-8")
    return folder


def bash_event(command: str) -> str:
    return json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})


# Push guard ------------------------------------------------------------


def test_k3_push_head_to_main_through_dash_c_is_denied(repo: Path, env: dict[str, str]) -> None:
    payload = json.dumps({"tool_name": "Bash", "cwd": str(repo),
                          "tool_input": {"command": f"git -C {repo} push origin HEAD:main"}})
    done = run(["python3", "-S", str(HOOKS / "push-guard.py")], repo, env, payload)
    assert done.returncode == 0, done.stderr
    decision = json.loads(done.stdout)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny", done.stdout


# Context cap ------------------------------------------------------------


def test_k6_cap_allows_hands_off_refuses_and_beats(tmp_path: Path) -> None:
    """builder-backend's 200k cap, a fresh agent per level: 50% silent, 90% and 100% denied.

    The crossing call at 90% carries the hand-off; past it the window lets Bash wrap up (gate.py).
    """
    at = {level: Tree(tmp_path / str(level)) for level in (100_000, 180_000, 200_000)}
    for level, tree in at.items():
        at_context(tree, level)
    assert gate(at[100_000], event(at[100_000])) is None
    assert (tmp_path / "100000" / "home" / ".claude" / "dev-loop" / "gate-heartbeat").is_file()
    assert "handover role=builder-backend" in denied(gate(at[180_000], event(at[180_000])))
    full = denied(gate(at[200_000], event(at[200_000])))
    assert "200k is at 90% of its 200k cap" in full, full


def test_k6_caps_disabled_says_nothing_at_the_cap(tmp_path: Path) -> None:
    tree = Tree(tmp_path)
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / "dev-loop.yaml").write_text("caps: {enabled: false}\n", "utf-8")
    at_context(tree, 200_000)
    assert gate(tree, event(tree)) is None


# Done guard ------------------------------------------------------------


def test_k7_done_guard_holds_once_then_lets_go(tmp_path: Path, env: dict[str, str]) -> None:
    root = tmp_path / "plain"  # no git: every finished change is in scope
    finished_change(root)
    env["CLAUDE_PROJECT_DIR"] = str(root)
    guard = ["sh", str(HOOKS / "done-guard.sh")]
    first = run(guard, root, env, "{}")
    assert first.returncode == 2, first
    assert "demo" in first.stderr and "verify" in first.stderr, first.stderr
    again = run(guard, root, env, '{"stop_hook_active": true}')
    assert (again.returncode, again.stdout, again.stderr) == (0, "", "")


def test_k8_a_broken_dl_is_one_line_and_exit_0(
        tmp_path: Path, repo: Path, env: dict[str, str]) -> None:
    copy = tmp_path / "installed"
    for part in ("hooks", "bin", "src"):
        shutil.copytree(PLUGIN / part, copy / part)
    (copy / "bin" / "dl").write_text("#!/usr/bin/env bash\nexit 3\n", encoding="utf-8")
    finished_change(repo)
    env["CLAUDE_PLUGIN_ROOT"] = str(copy)
    done = run(["sh", str(copy / "hooks" / "done-guard.sh")], repo, env, "{}")
    assert done.returncode == 0, done
    assert len(done.stderr.splitlines()) == 1, done.stderr
    assert done.stdout == ""


def test_k8_a_checkout_touching_no_change_is_silent(repo: Path, env: dict[str, str]) -> None:
    finished_change(repo)
    git(repo, env, "add", "-A")
    git(repo, env, "commit", "-qm", "old change")
    git(repo, env, "push", "-q", "origin", "HEAD:main")
    done = run(["sh", str(HOOKS / "done-guard.sh")], repo, env, "{}")
    assert (done.returncode, done.stdout, done.stderr) == (0, "", "")


# PR-loop trigger ------------------------------------------------------------


def trigger(repo: Path, env: dict[str, str], command: str) -> subprocess.CompletedProcess[str]:
    return run(["sh", str(HOOKS / "pr-loop-trigger.sh")], repo, env, bash_event(command))


def test_k9_a_real_create_nudges(repo: Path, env: dict[str, str]) -> None:
    done = trigger(repo, env, "dl pr demo")
    assert done.returncode == 2 and "[pr-loop]" in done.stderr, done


def test_k9_echo_dl_pr_stays_silent(repo: Path, env: dict[str, str]) -> None:
    assert trigger(repo, env, "echo dl pr").returncode == 0


def test_k9_a_file_named_x_dl_stays_silent(repo: Path, env: dict[str, str]) -> None:
    assert trigger(repo, env, "cat x.dl pr").returncode == 0


def test_k9_auto_trigger_off_stays_silent(repo: Path, env: dict[str, str]) -> None:
    (repo / "dev-loop.yaml").write_text("version: 1\nreview: {auto_trigger: false}\n", "utf-8")
    done = trigger(repo, env, "dl pr demo")
    assert (done.returncode, done.stderr) == (0, ""), done


# No PyYAML ------------------------------------------------------------


def has_yaml(python: str) -> bool:
    return run([python, "-c", "import yaml"], Path.cwd(), dict(os.environ)).returncode == 0


def minor(python: str) -> str:
    argv = [python, "-c", "import sys; print(sys.version_info[1])"]
    return run(argv, Path.cwd(), dict(os.environ)).stdout.strip()


def other_python() -> list[str] | None:
    """A Python of another minor than this one, without PyYAML: a system python3.

    One that has PyYAML runs with -S, which drops every site-packages folder and so PyYAML.
    """
    found = [shutil.which(f"python3.{n}") for n in range(9, 16)]
    if uv := shutil.which("uv"):
        for n in range(9, 16):
            done = run([uv, "python", "find", f"3.{n}"], Path.cwd(), dict(os.environ))
            found.append(done.stdout.strip() if done.returncode == 0 else None)
    mine = str(sys.version_info[1])
    other = next((p for p in found if p and minor(p) != mine), None)
    return None if other is None else [other] if not has_yaml(other) else [other, "-S"]


@pytest.fixture
def no_yaml(tmp_path: Path, env: dict[str, str]) -> Path:
    """The plugin venv (this Python, PyYAML in it) and a `python3` of another minor without it."""
    python = other_python()
    if python is None:
        pytest.skip("no Python of another minor without PyYAML here")
    venv = tmp_path / "data" / "venv"
    assert run([sys.executable, "-m", "venv", "--without-pip", str(venv)], tmp_path, env,
               timeout=120).returncode == 0
    purelib = "import sysconfig; print(sysconfig.get_paths()['purelib'])"
    site = run([str(venv / "bin" / "python"), "-c", purelib], tmp_path, env).stdout.strip()
    import yaml  # noqa: PLC0415 - the package this test copies into the venv

    shutil.copytree(Path(yaml.__file__).parent, Path(site) / "yaml")
    (venv / ".ready").touch()
    assert has_yaml(str(venv / "bin" / "python"))
    stub = tmp_path / "sysbin"
    stub.mkdir()
    (stub / "python3").write_text(f'#!/bin/sh\nexec {" ".join(python)} "$@"\n', "utf-8")
    (stub / "python3").chmod(0o755)
    env["PATH"] = f"{stub}{os.pathsep}{env['PATH']}"
    root = tmp_path / "project"
    root.mkdir()
    (root / "dev-loop.yaml").write_text("version: 1\nbriefs: {enforce: block}\n", "utf-8")
    env["CLAUDE_PROJECT_DIR"] = str(root)
    return root


@pytest.mark.xfail(strict=True, reason="AT K-5: no PyYAML under python3: exit 1, the call allowed")
def test_k5_block_holds_without_pyyaml(no_yaml: Path, env: dict[str, str]) -> None:
    payload = json.dumps({"tool_name": "Agent", "tool_input": {"prompt": "## Goal\nx\n"}})
    done = run(["python3", str(HOOKS / "brief-contract.py")], no_yaml, env, payload)
    assert done.returncode == 2, done


@pytest.mark.xfail(strict=True, reason="AT K-5: no PyYAML: plan-recall exits 1 and says nothing")
def test_k5_plan_recall_says_unavailable_without_pyyaml(
        no_yaml: Path, env: dict[str, str]) -> None:
    payload = json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": "/dev-loop:plan demo",
                          "cwd": str(no_yaml)})
    done = run(["python3", str(HOOKS / "plan-recall.py")], no_yaml, env, payload)
    assert "recall unavailable" in done.stdout, done


# Each hook's owner ------------------------------------------------------------

OWN = {"context gate": ("PreToolUse", "context-gate.py"), "done guard": ("Stop", "done-guard.sh"),
       "branch guard": ("PreToolUse", "branch-guard.sh"),
       "push guard": ("PreToolUse", "push-guard.py"),
       "pr loop": ("PostToolUse", "pr-loop-trigger.sh")}
SAID = ("two ", "duplicate", "twice", "second", "already")


def own_hooks() -> dict[str, object]:
    """A project's own copies of the five guards, registered as a settings file does."""
    hooks: dict[str, list[object]] = {}
    for event_name, script in OWN.values():
        command = {"type": "command", "command": f"sh .claude/hooks/{script}"}
        hooks.setdefault(event_name, []).append({"hooks": [command]})
    return {"hooks": hooks}


@pytest.mark.parametrize("where", [".claude/settings.json", ".claude/settings.local.json", "user"])
def test_k13_every_duplicate_hook_is_named(repo: Path, env: dict[str, str], where: str) -> None:
    path = Path(env["CLAUDE_CONFIG_DIR"]) / "settings.json" if where == "user" else repo / where
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(own_hooks()), encoding="utf-8")
    dl = ["bash", str(PLUGIN / "bin" / "dl")]
    said = ""
    for argv in (["sh", str(HOOKS / "session-guard.sh")], [*dl, "doctor"],
                 [*dl, "init", "--host", "local"]):
        done = run(argv, repo, env, timeout=120)
        said += done.stdout + done.stderr
    lines = [line.lower().replace("-", " ") for line in said.splitlines()]
    unnamed = [kind for kind in OWN
               if not any(kind in line and any(w in line for w in SAID) for line in lines)]
    assert not unnamed, unnamed


# Registered once ------------------------------------------------------------


def registered() -> list[str]:
    hooks = json.loads((HOOKS / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    commands = {hook["command"] for groups in hooks.values()
                for group in groups for hook in group["hooks"]}
    return sorted(commands)


PAYLOADS = {"empty": "", "not json": "not json {", "5 MB": json.dumps(
    {"hook_event_name": "PreToolUse", "tool_name": "Bash", "prompt": "x" * 5_000_000,
     "tool_input": {"command": "echo " + "x" * 5_000_000}})}


CASES = [pytest.param(command, payload)
         for command in registered() for payload in PAYLOADS]


@pytest.mark.parametrize(("command", "payload"), CASES)
def test_k15_every_hook_is_fast_and_fails_open(
        repo: Path, env: dict[str, str], command: str, payload: str) -> None:
    ready = Path(env["CLAUDE_PLUGIN_DATA"]) / "venv"
    ready.mkdir(parents=True)
    (ready / ".ready").touch()  # dl-bootstrap would build a venv from the network otherwise
    argv = ["sh", "-c", command.replace("${CLAUDE_PLUGIN_ROOT}", str(PLUGIN))]
    began = time.monotonic()
    done = run(argv, repo, env, PAYLOADS[payload], timeout=10)
    assert time.monotonic() - began < 10
    assert done.returncode in (0, 1, 2), done
    assert "Traceback" not in done.stdout, done.stdout[:400]


def test_k13_init_keeps_or_names_the_projects_own_git_hooks(
        repo: Path, env: dict[str, str]) -> None:
    own = repo / ".githooks" / "pre-commit"
    own.parent.mkdir()
    own.write_text("#!/bin/sh\necho own\n", encoding="utf-8")
    git(repo, env, "config", "core.hooksPath", ".husky")
    done = run(["bash", str(PLUGIN / "bin" / "dl"), "init", "--host", "local"], repo, env)
    hooks_path = run(["git", "config", "core.hooksPath"], repo, env).stdout.strip()
    said = (done.stdout + done.stderr).lower()
    assert own.read_text(encoding="utf-8").endswith("echo own\n"), "pre-commit overwritten"
    assert hooks_path == ".husky" or ".husky" in said, (hooks_path, said)


# Plan recall ------------------------------------------------------------


def test_k11_plan_recall_adds_past_lessons_and_records_them(
        tmp_path: Path, env: dict[str, str]) -> None:
    from test_recall import lesson, plant_lessons  # noqa: PLC0415 - the recall suite's planters

    root = tmp_path / "project"
    (root / ".dev-loop" / "changes" / "demo").mkdir(parents=True)
    (root / "dev-loop.yaml").write_text("version: 1\n", "utf-8")
    (root / ".dev-loop" / "changes" / "demo" / "proposal.md").write_text(
        "Rank lessons in `scripts/rank.py`.\n", "utf-8")
    plant_lessons(root, "past", lesson("L1", "rank lessons", "scripts/rank.py"))
    stub = tmp_path / "sysbin"
    stub.mkdir()
    (stub / "python3").write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', "utf-8")
    (stub / "python3").chmod(0o755)  # a python3 with PyYAML: its absence is tested above
    env |= {"CLAUDE_PROJECT_DIR": str(root), "PATH": f"{stub}{os.pathsep}{env['PATH']}"}
    payload = json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": "/dev-loop:plan demo",
                          "cwd": str(root)})
    done = run(["sh", "-c", f'python3 "{HOOKS / "plan-recall.py"}"'], root, env, payload)
    assert done.returncode == 0 and done.stdout, done
    context = json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "past:L1" in context, context
    records = (root / ".dev-loop" / "changes" / "demo" / "records.jsonl").read_text("utf-8")
    assert '"kind": "recall"' in records, records
