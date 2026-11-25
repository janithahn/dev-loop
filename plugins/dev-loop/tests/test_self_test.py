"""The SessionStart self-test names a broken hook at once, and is silent when all pass (0.4.18).

`hooks/self-test.py` runs the context gate, the brief contract and the push, branch, done and
rules guards as hooks.json registers them, each on a harmless event, plus `dl config get version`. A
hook is broken when it exits other than 0 or 2, runs past 5 s or adds a line to
`~/.claude/dev-loop/hook-errors.jsonl`. Every run here has its own HOME: a crash logs there.
Run: python -m pytest test_self_test.py -q (from the plugin's tests folder).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml
from test_neutral import check

PLUGIN = Path(__file__).resolve().parents[1]
# the list grew by design: the rules guard enforces (it denies a call), so it is tested too
NAMES = ["context-gate", "brief-contract", "push-guard", "branch-guard", "done-guard",
         "rules-guard", "dl config"]
# a hook that logs its own crash and still exits 0, as the push guard does
LOGS_AND_PASSES = """import sys
sys.path.insert(0, __file__.rsplit("/", 2)[0] + "/src")
from dev_loop.hookenv import crashed
crashed("push-guard", ValueError(), "command allowed")
"""


@pytest.fixture
def installed(tmp_path: Path) -> Path:
    """The plugin's hooks, source and launcher copied outside any project, as an install."""
    copy = tmp_path / "installed" / "dev-loop"
    for part in ("hooks", "src", "bin"):
        shutil.copytree(PLUGIN / part, copy / part, ignore=shutil.ignore_patterns("__pycache__"))
    (tmp_path / "repo").mkdir()
    return copy


def environment(installed: Path) -> dict[str, str]:
    """A session's environment: `python3` is this interpreter, HOME is the test's."""
    tmp = installed.parents[1]
    path = f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}"
    env = os.environ | {"HOME": str(tmp / "home"), "CLAUDE_PROJECT_DIR": str(tmp / "repo"),
                        "PATH": path, "DEV_LOOP_ROLE": "builder"}  # a runner's role is dropped
    for ambient in ("CLAUDE_PLUGIN_DATA", "CLAUDE_PLUGIN_ROOT", "CLAUDE_CONFIG_DIR"):
        env.pop(ambient, None)
    if (tmp / "data").exists():  # a case that made the plugin's data folder runs with it
        env["CLAUDE_PLUGIN_DATA"] = str(tmp / "data")
    return env


def run(installed: Path, *argv: str, budget: str = "30") -> subprocess.CompletedProcess[str]:
    """Run the self-test as the session does; `budget` is its per-hook timeout (load-proof)."""
    tmp = installed.parents[1]
    env = environment(installed) | {"DEV_LOOP_SELF_TEST_TIMEOUT": budget}
    return subprocess.run(  # noqa: S603 - this interpreter and our own hook
        [sys.executable, str(installed / "hooks" / "self-test.py"), *argv], input="{}",
        capture_output=True, text=True, check=False, timeout=120, env=env, cwd=tmp / "repo")


def state(installed: Path) -> Path:
    return installed.parents[1] / "home" / ".claude" / "dev-loop"


def said(done: subprocess.CompletedProcess[str]) -> str:
    assert done.returncode == 0, done
    answer = json.loads(done.stdout)
    assert set(answer) == {"systemMessage"}, answer
    return str(answer["systemMessage"])


def test_all_hooks_passing_is_silent_and_leaves_no_trace(installed: Path) -> None:
    done = run(installed)
    assert (done.returncode, done.stdout, done.stderr) == (0, "", ""), done
    assert not (state(installed) / "gate-heartbeat").exists()  # the gate judged nothing
    assert not (state(installed) / "hook-errors.jsonl").exists()


def test_a_hook_whose_import_fails_is_named(installed: Path) -> None:
    guard = installed / "hooks" / "push-guard.py"
    guard.write_text("import no_such_module_for_this_test\n" + guard.read_text("utf-8"), "utf-8")
    message = "dev-loop self-test: broken hooks: push-guard; run dl doctor"
    assert said(run(installed)) == message


def test_a_broken_rules_guard_is_named(installed: Path) -> None:
    guard = installed / "hooks" / "rules-guard.py"
    guard.write_text("import no_such_module_for_this_test\n" + guard.read_text("utf-8"), "utf-8")
    message = "dev-loop self-test: broken hooks: rules-guard; run dl doctor"
    assert said(run(installed)) == message


def test_every_python_hook_that_can_deny_a_call_is_tested(installed: Path) -> None:
    """Whatever hooks.json registers under PreToolUse: a new one is listed or this fails."""
    groups = json.loads((PLUGIN / "hooks" / "hooks.json").read_text("utf-8"))["hooks"]
    names = {name for group in groups["PreToolUse"] for hook in group["hooks"]
             for name in re.findall(r"/hooks/([\w-]+)\.py", hook["command"])}
    listed = {line.split(" ", 1)[1] for line in run(installed, "--list").stdout.splitlines()}
    assert "rules-guard" in names and names <= listed, names - listed


def test_a_hook_that_logs_a_crash_and_exits_zero_is_named(installed: Path) -> None:
    (installed / "hooks" / "push-guard.py").write_text(LOGS_AND_PASSES, "utf-8")
    assert said(run(installed)) == "dev-loop self-test: broken hooks: push-guard; run dl doctor"


def test_a_log_line_of_a_hook_this_run_did_not_start_is_not_named(installed: Path) -> None:
    """The log is every live session's: a line naming a hook the self-test did not start (here
    the approval hook) is another session's, and is neither a broken hook nor a new name."""
    other = LOGS_AND_PASSES.replace('"push-guard"', '"approval"')
    (installed / "hooks" / "push-guard.py").write_text(other, "utf-8")
    done = run(installed, "--list")
    assert done.stdout.splitlines() == [f"ok {name}" for name in NAMES], done
    assert run(installed).stdout == ""


def test_a_slow_hook_under_five_seconds_is_not_named(installed: Path) -> None:
    (installed / "hooks" / "done-guard.sh").write_text("sleep 3\n", "utf-8")  # a loaded machine
    done = run(installed, budget="5")
    assert (done.returncode, done.stdout, done.stderr) == (0, "", ""), done


def test_a_hook_past_five_seconds_is_named_and_the_session_is_not_held(installed: Path) -> None:
    (installed / "hooks" / "done-guard.sh").write_text("sleep 20\n", "utf-8")
    start = time.monotonic()
    message = said(run(installed, budget="5"))
    assert time.monotonic() - start < 9  # noqa: PLR2004 - the 5 s budget plus start-up
    assert message == "dev-loop self-test: broken hooks: done-guard; run dl doctor"


def test_a_launcher_that_fails_is_named(installed: Path) -> None:
    (installed / "bin" / "dl").write_text("exit 3\n", "utf-8")
    assert "dl config" in said(run(installed))  # the guards that call it may be named beside it


def test_a_venv_without_pyyaml_names_the_context_gate(installed: Path) -> None:
    """The gate's main-session event needs no PyYAML, so the hook itself passes; judging an agent
    needs the config, the gate and the scanner under `python3 -S` with the plugin venv alone."""
    (installed.parents[1] / "data" / "venv").mkdir(parents=True)  # a venv holding no PyYAML
    lines = run(installed, "--list").stdout.splitlines()
    assert lines == ["broken context-gate", *(f"ok {name}" for name in NAMES[1:])], lines
    assert said(run(installed)) == "dev-loop self-test: broken hooks: context-gate; run dl doctor"
    assert not (state(installed) / "hook-errors.jsonl").exists()  # the probe logs nothing


# a bootstrap still at work when the session's other hooks start: it finishes the venv a second on
LATE_BOOTSTRAP = """sleep 1
packages="$CLAUDE_PLUGIN_DATA/venv/lib/python3.0/site-packages"
mkdir -p "$packages" && cp -r "$YAML" "$packages/yaml" && touch "$CLAUDE_PLUGIN_DATA/venv/.ready"
"""


def test_a_first_session_with_a_half_built_venv_raises_no_alarm(installed: Path) -> None:
    """A SessionStart's commands start side by side, so the bootstrap and the self-test are one
    command, in that order: the self-test never judges a venv the bootstrap is still building."""
    (installed.parents[1] / "data" / "venv").mkdir(parents=True)  # no .ready, no PyYAML yet
    (installed / "bin" / "dl-bootstrap").write_text(LATE_BOOTSTRAP, "utf-8")
    groups = json.loads((installed / "hooks" / "hooks.json").read_text("utf-8"))["hooks"]
    commands = [hook["command"] for group in groups["SessionStart"] for hook in group["hooks"]
                if "dl-bootstrap" in hook["command"] or "self-test.py" in hook["command"]]
    env = environment(installed) | {"CLAUDE_PLUGIN_ROOT": installed.as_posix(),
                                    "YAML": str(Path(yaml.__file__).parent)}
    started = [subprocess.Popen(["bash", "-c", command], stdin=subprocess.PIPE,  # noqa: S603, S607
                                stdout=subprocess.PIPE, text=True, env=env)
               for command in commands]
    said = [process.communicate("{}", timeout=60)[0] for process in started]
    assert commands and not any("systemMessage" in out for out in said), said
    assert all(process.returncode == 0 for process in started)
    assert not (state(installed) / "hook-errors.jsonl").exists()


def test_a_launcher_that_finds_no_python_is_named(installed: Path) -> None:
    """bin/dl exits 2 when no Python has PyYAML. 2 is a guard's "blocked", never the launcher's."""
    (installed / "bin" / "dl").write_text("exit 2\n", "utf-8")
    assert "dl config" in said(run(installed))


def test_list_prints_one_line_per_hook(installed: Path) -> None:
    done = run(installed, "--list")
    assert (done.returncode, done.stderr) == (0, ""), done
    assert done.stdout.splitlines() == [f"ok {name}" for name in NAMES]
    (installed / "hooks" / "branch-guard.sh").write_text("exit 1\n", "utf-8")
    lines = run(installed, "--list").stdout.splitlines()
    assert "broken branch-guard" in lines and len(lines) == len(NAMES), lines


def test_its_own_crash_never_blocks_the_session_and_is_said(installed: Path) -> None:
    (installed / "hooks" / "hooks.json").write_text("{", "utf-8")
    done = run(installed)
    assert said(done) == "dev-loop self-test could not run: JSONDecodeError; run dl doctor"
    (row,) = (state(installed) / "hook-errors.jsonl").read_text("utf-8").splitlines()
    assert (json.loads(row)["hook"], json.loads(row)["error"]) == ("self-test", "JSONDecodeError")


def test_the_files_are_neutral() -> None:
    assert check([Path(__file__), PLUGIN / "hooks" / "self-test.py"]) == []
