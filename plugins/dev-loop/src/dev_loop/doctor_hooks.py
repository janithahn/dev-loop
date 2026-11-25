"""`dl doctor`'s hook rows: what the hooks logged in the last day, and the self-test's verdict.

A hook fails open, so a broken one blocks nothing and is seen by nobody. `hook errors` is red
while ~/.claude/dev-loop/hook-errors.jsonl or gate-errors.jsonl holds a line of the last 24 h,
naming each hook, error class and count (never text). `hook self-test` runs
`hooks/self-test.py --list`: every guard once on a harmless event, red on a broken one. The
context-gate row reads the same run: self_test_lines keeps one per plugin, and `dl doctor` forgets
it as each run of its rows starts, so no verdict outlives the run that asked for it.
"""

from __future__ import annotations

import functools
import subprocess
import sys
import time
from pathlib import Path

from dev_loop import hookenv

DAY = 86400
SELF_TEST_TIMEOUT = 30  # seconds; the self-test's own budget is 5
Row = tuple[str, bool, str]


def errors_row() -> Row:
    found = hookenv.errors_since(time.time() - DAY)
    if not found:
        return ("hook errors", True, "none in the last 24 h")
    named = ", ".join(f"{hook} {error} x{count}" for (hook, error), count in sorted(found.items()))
    total = sum(found.values())
    detail = f"{total} in the last 24 h: {named} (clears a day after the last)"
    return ("hook errors", False, detail)


@functools.cache  # one run a doctor run: doctor.rows clears it first
def self_test_lines(plugin: Path) -> list[str] | None:
    command = [sys.executable, str(plugin / "hooks" / "self-test.py"), "--list"]
    try:
        done = subprocess.run(command, capture_output=True, text=True,  # noqa: S603
                              check=False, timeout=SELF_TEST_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout.splitlines()


def gate_state(plugin: Path) -> str | None:
    """'ok' or 'broken' as the self-test names the context gate; None when it did not say."""
    for line in self_test_lines(plugin) or []:
        if line.endswith(" context-gate") and line.split(" ")[0] in ("ok", "broken"):
            return line.split(" ")[0]
    return None


def self_test_row(plugin: Path) -> Row:
    lines = self_test_lines(plugin)
    if lines is None:
        return ("hook self-test", False, "did not run")
    broken = [line.removeprefix("broken ") for line in lines if line.startswith("broken ")]
    passed = sum(line.startswith("ok ") for line in lines)
    if broken:
        return ("hook self-test", False, "broken: " + ", ".join(broken))
    return ("hook self-test", passed > 0, f"{passed} hooks ok" if passed else "did not run")


def rows(plugin: Path) -> list[Row]:
    return [self_test_row(plugin), errors_row()]  # the self-test first: its crashes are logged
