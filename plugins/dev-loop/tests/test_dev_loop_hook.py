"""Self-check for the dashboard's restart and its state's text.

The HTTP hook route went: `.claude/hooks/context-gate.py` enforces, checked in
`test_dev_loop_context_*.py`.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from dev_loop.dashboard import scan
from test_dev_loop_fixtures import SECRET, SESSION, Tree, assistant, run, user
from test_dev_loop_serving import DL, call, pause, serving

if TYPE_CHECKING:
    from collections.abc import Callable


def check_restart_revives_the_scanner(tree: Tree) -> None:
    with serving(tree) as (app, port):
        app.workers.interval = 0.01
        assert not app.workers.wake.is_set()
        _, state = call(port, ("GET", "/api/state"))
        assert state["threads"] == {}  # nothing started yet
        status, answer = call(port, ("POST", "/api/restart"), None, DL)
        assert status == 200
        assert answer["restarted"] == ["scanner"]
        deadline = time.time() + 5
        while scan.RESTART.is_set() and time.time() < deadline:
            time.sleep(0.01)
        _, state = call(port, ("GET", "/api/state"))
        assert state["threads"] == {"scanner": True}
        assert call(port, ("POST", "/api/restart"), None, DL)[1]["restarted"] == []  # still alive


def check_no_text_in_the_state(tree: Tree) -> None:
    tree.write([user(), assistant("m1", tool="Bash")])
    with serving(tree) as (_, port):
        pause(port, SESSION)
        raw = repr(call(port, ("GET", "/api/state")))
        assert SECRET not in raw


if __name__ == "__main__":
    checks: list[Callable[[Tree], None]] = [
        obj for name, obj in list(globals().items()) if name[:6] == "check_"
    ]
    run("dev-loop hook", checks)
