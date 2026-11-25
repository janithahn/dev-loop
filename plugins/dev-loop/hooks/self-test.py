"""SessionStart self-test: run each guard once on a harmless event and name the broken ones.

A hook fails open, so a broken one lets every call through; this says so when the session
starts. The context gate, the brief contract and the push, branch, done and rules guards run in
parallel, each by the command hooks.json registers for it, plus `dl config get version` (the
launcher the guards call). A guard is broken when it exits other than 0 or 2 (its "blocked");
the launcher and IMPORTS when they exit other than 0 (the launcher's 2 is "no Python with
PyYAML"); any of them when it is still running after TIMEOUT or adds a line to
~/.claude/dev-loop/hook-errors.jsonl under its own name (the log is every live session's: a
line naming a hook not started here is another's). The gate's event is a main session's (no
agent, no role): it is never judged, so the gate's heartbeat stays proof of a real call. That
event needs none of what judging an agent needs, so IMPORTS loads those as the gate does
(`python3 -S`, the plugin venv's packages) and a failure there names `context-gate` too.
The done guard's event is a Stop it already held, so it reads no change folder. The rules
guard's event names no guarded file, so it loads no config: PyYAML missing is IMPORTS' to name.

hooks.json runs it in the bootstrap's own command, after it: a session's commands start side by
side, and a venv the bootstrap is still building would be named broken on a first session.

Broken hooks: one `systemMessage` naming them and `dl doctor`. All pass: nothing. It never blocks
the session: exit 0 always, its own crash included (said the same way, and logged).
`--list` prints `ok <hook>` or `broken <hook>`, a line each, for `dl doctor`.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
# seconds: each hook's and the whole run's (they run side by side); a loaded machine is slow.
# DEV_LOOP_SELF_TEST_TIMEOUT raises it where a host is known to be busy (and in its own tests).
TIMEOUT = float(os.environ.get("DEV_LOOP_SELF_TEST_TIMEOUT") or 5)
LOG = Path.home() / ".claude" / "dev-loop" / "hook-errors.jsonl"  # as dev_loop.hookenv names it
DL = 'bash "${CLAUDE_PLUGIN_ROOT}/bin/dl" config get version'
# what the gate imports to judge an agent's call, found as the gate finds it; exit 0 or broken
IMPORTS = (
    'python3 -S -c "import sys; sys.path.insert(0, sys.argv[1]); '
    "from dev_loop.hookenv import add_venv; add_venv(); "
    'import dev_loop.config, dev_loop.dashboard.gate, dev_loop.dashboard.scan" '
    '"${CLAUDE_PLUGIN_ROOT}/src"'
)
EVENTS: dict[str, dict[str, object]] = {  # the hook's file: the harmless event it reads on stdin
    "context-gate.py": {"session_id": "self-test", "tool_name": "Bash", "tool_input": {}},
    "brief-contract.py": {"tool_name": "Agent", "tool_input": {"prompt": ""}},
    "push-guard.py": {"tool_name": "Bash", "tool_input": {"command": "true"}},
    "branch-guard.sh": {"tool_name": "Edit", "tool_input": {"file_path": ""}},
    "done-guard.sh": {"stop_hook_active": True},
    "rules-guard.py": {"tool_name": "Bash", "tool_input": {"command": "true"}},
}


def checks() -> dict[str, tuple[str, dict[str, object]]]:
    """Return each hook's name, its command exactly as hooks.json registers it, and its event."""
    events = json.loads((PLUGIN / "hooks" / "hooks.json").read_text("utf-8"))["hooks"]
    every = [hook["command"] for groups in events.values() for group in groups
             for hook in group["hooks"]]
    found: dict[str, tuple[str, dict[str, object]]] = {Path(name).stem: (next(one for one in every if f"/hooks/{name}" in one), event)
             for name, event in EVENTS.items()}
    return found | {"dl config": (DL, {}), "gate imports": (IMPORTS, {})}


def environment() -> dict[str, str]:
    """Return the session's environment, the plugin being this one and no runner's role."""
    env = os.environ | {"CLAUDE_PLUGIN_ROOT": PLUGIN.as_posix()}
    env.pop("DEV_LOOP_ROLE", None)  # a role would have the gate judge the canned event
    if "CLAUDE_PLUGIN_DATA" not in env:  # outside a session (dl doctor): where bin/dl looks
        config = env.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude"
        env["CLAUDE_PLUGIN_DATA"] = Path(config, "plugins", "data", "dev-loop-dev-loop").as_posix()
    return env


def logged() -> list[str]:
    try:
        return LOG.read_text("utf-8").splitlines()
    except OSError:
        return []


def results() -> dict[str, bool]:
    """Run every hook at once; return each name and whether it passed."""
    before, env, wanted = len(logged()), environment(), checks()
    quiet = subprocess.DEVNULL
    started = {
        name: subprocess.Popen(["bash", "-c", command], stdin=subprocess.PIPE,  # noqa: S603, S607
                               stdout=quiet, stderr=quiet, env=env)
        for name, (command, _) in wanted.items()
    }
    for name, process in started.items():
        with contextlib.suppress(OSError):  # a hook that died before reading: its exit code says it
            process.stdin.write(json.dumps(wanted[name][1]).encode())  # type: ignore[union-attr]
            process.stdin.close()  # type: ignore[union-attr]
    deadline = time.monotonic() + TIMEOUT
    passed: dict[str, bool] = {}
    for name, process in started.items():
        try:
            allowed = (0, 2) if f"{name}.py" in EVENTS or f"{name}.sh" in EVENTS else (0,)
            passed[name] = process.wait(max(0.0, deadline - time.monotonic())) in allowed
        except subprocess.TimeoutExpired:
            process.kill()  # ponytail: bash execs a single command, so this is the hook itself
            passed[name] = False
    # ponytail: a log rotated during the run hides its new lines; the exit codes still count
    # ponytail: another session's crash of a hook started here, logged in these seconds, is ours
    for line in logged()[before:]:
        with contextlib.suppress(ValueError, AttributeError, TypeError):
            if (name := json.loads(line).get("hook")) in passed:
                passed[name] = False
    passed["context-gate"] = passed.pop("gate imports") and passed["context-gate"]
    return passed


def main(argv: list[str]) -> None:
    passed = results()
    if "--list" in argv:
        for name, ok in passed.items():
            print(f"{'ok' if ok else 'broken'} {name}")  # noqa: T201 - doctor reads the lines
        return
    broken = [name for name, ok in passed.items() if not ok]
    if broken:
        said = f"dev-loop self-test: broken hooks: {', '.join(broken)}; run dl doctor"
        print(json.dumps({"systemMessage": said}))  # noqa: T201 - stdout is the hook's answer


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except BaseException as error:  # noqa: BLE001 - the session starts whatever happens here
        said = f"dev-loop self-test could not run: {type(error).__name__}; run dl doctor"
        print(json.dumps({"systemMessage": said}))  # noqa: T201 - said first: it needs nothing
        with contextlib.suppress(BaseException):  # the log needs the plugin's source: best effort
            sys.path.insert(0, str(PLUGIN / "src"))
            from dev_loop.hookenv import crashed

            crashed("self-test", error, "hooks not tested")
    sys.exit(0)
