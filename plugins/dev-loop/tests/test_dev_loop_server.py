"""Self-check for the dev-loop dashboard's page state, controls and settings.

Each check starts a real server on a free local port over fixture transcripts. Run: python tests/test_dev_loop_server.py; test_self_checks.py runs it.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

from dev_loop.dashboard import scan, store
from dev_loop.dashboard.gate import Controls
from dev_loop.dashboard.scan import Scanner
from dev_loop.dashboard.server import GATE
from dev_loop.dashboard.store import Store, merge_settings
from test_dev_loop_fixtures import (
    AGENT,
    NOW,
    SECRET,
    SESSION,
    Tree,
    assistant,
    line,
    run,
    scanned,
    stamp,
    tool_result,
    user,
)
from test_dev_loop_serving import DL, call, pause, serving

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

DENIED = "denied"


def check_the_stale_page_and_the_pause_only_flags(tree: Tree) -> None:
    tree.write([user(), assistant("m1", "end_turn")])
    with serving(tree) as (app, port):
        _, state = call(port, ("GET", "/api/state"))
        assert state["stale"] is False
        assert state["pauses_only"] is False
        assert state["scan_error"] is None
        assert state["scan_age"] < 30
        app.scanner.scanned_at = time.time() - 31
        _, state = call(port, ("GET", "/api/state"))
        assert (state["stale"], state["pauses_only"]) == (True, False)
        assert state["scan_age"] > 30
        app.scanner.scanned_at = time.time() - 61
        _, state = call(port, ("GET", "/api/state"))
        assert (state["stale"], state["pauses_only"]) == (True, True)
        app.scanner.scanned_at = time.time()  # age 0: the gate enforces caps, so does the page
        _, state = call(port, ("GET", "/api/state"))
        assert state["pauses_only"] is False
        app.scanner.scanned_at = 0.0  # no pass has completed: never looks live
        _, state = call(port, ("GET", "/api/state"))
        assert (state["stale"], state["pauses_only"], state["scan_age"]) == (True, True, None)


def check_a_failing_pass_shows_its_error_then_clears(tree: Tree) -> None:
    scanner = scanned(tree)
    original = Scanner.scan

    def failing(_: Scanner) -> Iterator[scan.Transcript]:
        raise PermissionError(DENIED)

    Scanner.scan = failing  # type: ignore[method-assign,assignment]
    try:
        scanner.refresh()
        with serving(tree, scanner=scanner) as (_, port):
            _, state = call(port, ("GET", "/api/state"))
            assert "PermissionError" in state["scan_error"]
            Scanner.scan = original  # type: ignore[method-assign]
            scanner.refresh()
            _, state = call(port, ("GET", "/api/state"))
            assert state["scan_error"] is None
    finally:
        Scanner.scan = original  # type: ignore[method-assign]


def check_a_bad_value_is_a_400_with_its_reason_and_the_file_is_unchanged(tree: Tree) -> None:
    with serving(tree) as (_, port):
        assert pause(port, "s1")[0] == 200
        path = tree.root / "owner" / "control.json"
        before = path.read_bytes()
        scopes = {"budget": "daily", "context": "default_agent"}
        bad: list[dict[str, Any]] = [
            {"action": action, "scope": scope, "value": value}
            for action, scope in scopes.items()
            for value in (0, -1, "abc", True, 1e99 if action == "context" else float("nan"))
        ]
        bad += [
            {"action": "budget", "scope": "row", "id": "s1", "value": 0},
            {"action": "context", "scope": "row", "id": "s1", "value": 2_000_000},
            {"action": "budget", "scope": "row", "value": 5},
            {"action": "nope"},
            {"action": "budget", "scope": "elsewhere", "value": 5},
            {"action": "auto_compact", "value": 5},
            {"action": ["pause"], "id": "s1"},
        ]
        for body in bad:
            status, answer = call(port, ("POST", "/api/control"), body, DL)
            assert status == 400, body
            assert answer["error"], body
            assert path.read_bytes() == before, body
        assert call(port, ("POST", "/api/control"), b"[1]", DL)[0] == 400
        assert call(port, ("POST", "/api/control"), b"{broken", DL)[0] == 400
        assert path.read_bytes() == before


def check_valid_controls_apply_and_empty_clears_a_cap(tree: Tree) -> None:
    with serving(tree) as (app, port):
        sets: list[dict[str, Any]] = [  # a row's context cap is the one cap left
            {"action": "context", "scope": "row", "id": SESSION, "value": 300_000},
            {"action": "context", "scope": "row", "id": AGENT, "value": 200_000},
        ]
        for body in sets:
            assert call(port, ("POST", "/api/control"), body, DL)[0] == 200
        assert app.store.controls.context_caps == {SESSION: 300_000, AGENT: 200_000}
        clear = {**sets[1], "value": ""}
        assert call(port, ("POST", "/api/control"), clear, DL)[0] == 200
        assert app.store.controls.context_caps == {SESSION: 300_000}
        assert call(port, ("POST", "/api/control"), {**sets[0], "value": None}, DL)[0] == 200
        assert app.store.controls.context_caps == {}
        assert pause(port, "s1")[0] == 200
        resume = {"action": "resume", "id": "s1"}
        assert call(port, ("POST", "/api/control"), resume, DL)[0] == 200
        assert app.store.controls.paused == frozenset()
        assert Store(tree.root / "owner" / "control.json").controls == app.store.controls


def check_a_session_is_running_while_one_of_its_agents_runs(tree: Tree) -> None:
    tree.write([user(), assistant("m1", "end_turn")])
    tree.write([user(), assistant("m2", "tool_use", tool="Bash")], agent=AGENT)
    with serving(tree) as (_, port):
        _, state = call(port, ("GET", "/api/state"))
        status = {row["id"]: row["status"] for row in state["sessions"]}
        assert status == {SESSION: "running", AGENT: "running"}


def check_a_failing_state_build_is_a_500_with_its_reason(tree: Tree) -> None:
    with serving(tree) as (app, port):
        app.spend = lambda: (_ for _ in ()).throw(RuntimeError("no totals"))  # type: ignore[method-assign]
        status, answer = call(port, ("GET", "/api/state"))
        assert status == 500
        assert "RuntimeError: no totals" in answer["error"]


def check_a_post_needs_the_header_and_a_local_host(tree: Tree) -> None:
    with serving(tree) as (app, port):
        body = {"action": "pause", "id": "s1"}
        assert call(port, ("POST", "/api/control"), body)[0] == 403
        assert call(port, ("POST", "/api/restart"))[0] == 403
        assert not scan.RESTART.is_set()
        assert app.store.controls == Controls()
        rebound = {**DL, "Host": f"evil.example:{port}"}
        assert call(port, ("POST", "/api/control"), body, rebound)[0] == 403
        assert call(port, ("GET", "/api/state"), None, {"Host": "evil.example"})[0] == 403
        assert call(port, ("POST", "/hook/pre-tool-use"), {"session_id": "x"}, rebound)[0] == 403
        assert app.store.controls == Controls()
        assert call(port, ("GET", "/elsewhere"))[0] == 404  # "/" is the page since part c1


def check_the_state_shows_whether_the_hook_is_wired_and_a_bad_settings_file(tree: Tree) -> None:
    with serving(tree) as (app, port):
        app.root = tree.root  # the project's settings.json, under the dashboard's root
        settings = tree.root / ".claude" / "settings.json"
        settings.parent.mkdir()
        _, state = call(port, ("GET", "/api/state"))
        assert (state["hook_wired"], state["settings_error"]) == (False, None)
        gate = {"type": "command", "command": f"python -S .claude/hooks/{GATE}"}
        merge_settings(settings, lambda s: s.update(hooks={"PreToolUse": [{"hooks": [gate]}]}))
        _, state = call(port, ("GET", "/api/state"))
        assert (state["hook_wired"], state["settings_error"]) == (True, None)
        settings.write_text("{broken", encoding="utf-8")
        _, state = call(port, ("GET", "/api/state"))  # shown, never read as "not wired"
        assert state["hook_wired"] is None
        assert "does not parse" in state["settings_error"]


def check_hook_wired_reads_the_project_settings(tree: Tree) -> None:
    """The hook is registered in the project's settings.json, never the user's."""
    gate = {"hooks": [{"type": "command", "command": f"python -S .claude/hooks/{GATE}"}]}
    with serving(tree) as (app, port):
        app.root = tree.root
        user_file = tree.root / "owner" / "settings.json"  # the serving helper's user settings
        merge_settings(user_file, lambda s: s.update(hooks={"PreToolUse": [gate]}))
        assert call(port, ("GET", "/api/state"))[1]["hook_wired"] is False
        settings = tree.root / ".claude" / "settings.json"
        settings.parent.mkdir()
        merge_settings(settings, lambda s: s.update(hooks={"PostToolUse": [gate]}))
        assert call(port, ("GET", "/api/state"))[1]["hook_wired"] is False  # not PreToolUse
        merge_settings(settings, lambda s: s.update(hooks={"PreToolUse": [gate]}))
        assert call(port, ("GET", "/api/state"))[1]["hook_wired"] is True


def check_state_rows_carry_stated_clamped_default(tree: Tree) -> None:
    """Stated, clamped and default caps beside the one the hook applies; its windows."""
    stated = user("dev-loop budget: role=planner context=900k\nthe task")
    agents = {AGENT: [stated], "a2": [user()], "a3": [user("dev-loop budget: context=150k")]}
    tree.write([stated, assistant("m0", "end_turn")])  # a session: stated or not, never capped
    for agent, records in agents.items():
        path = tree.write([*records, assistant("m1", "end_turn")], agent=agent)
        meta = {"agentType": "dev-loop:builder-backend" if agent != "a3" else "Explore"}
        path.with_name(f"agent-{agent}.meta.json").write_text(json.dumps(meta), encoding="utf-8")
    with serving(tree) as (app, port):
        app.root = tree.root
        (tree.root / "owner" / "windows").mkdir()
        (tree.root / "owner" / "windows" / AGENT).write_bytes(b"...")
        rows = {r["id"]: r for r in call(port, ("GET", "/api/state"))[1]["sessions"]}
        clamped = {"context": 400_000, "stated": 900_000, "default": 200_000, "clamped": True}
        assert rows[AGENT]["caps"] == clamped, rows[AGENT]
        none = {"context": 200_000, "stated": None, "default": 200_000, "clamped": False}
        assert rows["a2"]["caps"] == none, rows["a2"]
        kept = {"context": 150_000, "stated": 150_000, "default": 200_000, "clamped": False}
        assert (rows["a3"]["role"], rows["a3"]["caps"]) == ("general", kept), rows["a3"]
        session = {"context": None, "stated": None, "default": None, "clamped": False}
        assert rows[SESSION]["caps"] == session, rows[SESSION]
        body = {"action": "context", "scope": "row", "id": AGENT, "value": 120_000}
        assert call(port, ("POST", "/api/control"), body, DL)[0] == 200
        state = call(port, ("GET", "/api/state"))[1]
        owner = next(r for r in state["sessions"] if r["id"] == AGENT)["caps"]
        assert owner == {**clamped, "context": 120_000}, owner  # the owner's cap is applied
        assert state["windows"] == {AGENT: 3}, state["windows"]
        assert SECRET not in json.dumps(state)


def check_state_rows_carry_started(tree: Tree) -> None:
    """T-d5: a row's start is its first timestamped record, epoch seconds; none yet: None."""
    first = {**user(), "timestamp": stamp(7200)}
    tree.write([tool_result(), first, assistant("m1", "end_turn", at=stamp(60))])
    tree.write([{"type": "custom-title", "customTitle": "untimed"}], agent=AGENT)
    with serving(tree) as (_, port):
        rows = {r["id"]: r for r in call(port, ("GET", "/api/state"))[1]["sessions"]}
        assert abs(rows[SESSION]["started"] - (NOW - 7200)) < 1, rows[SESSION]
        assert rows[AGENT]["started"] is None, rows[AGENT]


def check_state_shows_hook_errors(tree: Tree) -> None:
    """gate-errors.jsonl (and its .1) counted, a bad line skipped; ids and classes only."""
    owner = tree.root / "owner"
    with serving(tree) as (_, port):
        assert call(port, ("GET", "/api/state"))[1]["hook_errors"] == {"count": 0, "last": []}
        rows: list[dict[str, object]] = [
            {"at": float(n), "error": "OSError", "frame": None} for n in range(7)
        ]
        (owner / "gate-errors.1.jsonl").write_bytes(line(rows[0]).encode() + b"\n")
        leaked = {"at": 7.0, "error": "KeyError", "frame": "context-gate:judge", "message": SECRET}
        planted = [*map(line, rows[1:]), "not json {", "[1]", line(leaked)]
        (owner / "gate-errors.jsonl").write_bytes("\n".join([*planted, ""]).encode())
        found = call(port, ("GET", "/api/state"))[1]
        newest = {"at": 7.0, "error": "KeyError", "frame": "context-gate:judge"}
        shown = {"count": len(rows) + 1, "last": [*rows[3:], newest]}  # 2 bad lines skipped
        assert found["hook_errors"] == shown, found["hook_errors"]
        assert SECRET not in json.dumps(found)


def check_the_old_hook_route_is_gone(tree: Tree) -> None:
    """The dashboard enforces nothing; a paused session's event at the old route is a 404."""
    with serving(tree) as (app, port):
        assert pause(port, SESSION)[0] == 200
        event = {"session_id": SESSION, "agent_id": AGENT, "tool_name": "Bash"}
        for headers in (None, DL):
            status, answer = call(port, ("POST", "/hook/pre-tool-use"), event, headers)
            assert (status, answer) == (404, {"error": "not found"}), headers
        assert not hasattr(app, "decide")
        assert not hasattr(app, "hook")
        assert not hasattr(store, "wire")


def check_a_bad_control_body_is_a_400_with_its_reason(tree: Tree) -> None:
    with serving(tree) as (app, port):
        for raw in (b"[1]", b"{broken", b'{"action": "budget", "scope": "daily", "value": 0}'):
            status, answer = call(port, ("POST", "/api/control"), raw, DL)
            assert status == 400, raw
            assert answer["error"], raw
        assert app.store.controls == Controls()
        assert not (tree.root / "owner" / "control.json").exists()


if __name__ == "__main__":
    checks: list[Callable[[Tree], None]] = [
        obj for name, obj in list(globals().items()) if name[:6] == "check_"
    ]
    run("dev-loop server", checks)
