"""Self-check for the dashboard's control storage and settings merge.

Concurrent writes, durable replace, a bad file stopping the start, validation and the merge into
`~/.claude/settings.json`. Run: python tests/test_dev_loop_store.py; test_self_checks.py runs it.
"""

from __future__ import annotations

import json
import os
import threading
from typing import TYPE_CHECKING, Any

from dev_loop.dashboard.gate import Controls
from dev_loop.dashboard.store import (
    Store,
    edit,
    merge_settings,
    settings_view,
    write_durable,
)
from test_dev_loop_fixtures import Tree, run

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

URL = "http://127.0.0.1:8787/hook/pre-tool-use"
OLD_HOOK = {"matcher": "*", "hooks": [{"type": "http", "url": URL}]}  # the old --install-hook's
HELD = "held"
UNREPORTED = "the failed write was reported done"
OLD_KEYS = ("daily_usd", "session_usd", "agent_usd", "session_context", "agent_context")


def owned(tree: Tree) -> Path:
    owner = tree.root / "owner"
    owner.mkdir(exist_ok=True)
    return owner


def apply(store: Store, body: dict[str, Any]) -> Controls:
    return store.change(lambda controls: edit(controls, body))


def check_30_concurrent_pauses_all_land(tree: Tree) -> None:
    store = Store(owned(tree) / "control.json")
    ids = [f"s{n}" for n in range(30)]
    workers = [
        threading.Thread(target=apply, args=(store, {"action": "pause", "id": i})) for i in ids
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    stored = json.loads((owned(tree) / "control.json").read_text("utf-8"))
    assert sorted(stored["paused"]) == sorted(ids)
    assert store.controls.paused == frozenset(ids)
    assert Store(owned(tree) / "control.json").controls == store.controls


def check_a_failed_replace_is_retried_and_a_dead_one_changes_nothing(tree: Tree) -> None:
    target = owned(tree) / "file.json"
    real, failures = os.replace, list[int]()

    def flaky(src: Path, dst: Path) -> None:
        if len(failures) < 2:
            failures.append(1)
            raise PermissionError(HELD)
        real(src, dst)

    def dead(_src: Path, _dst: Path) -> None:
        raise PermissionError(HELD)

    os.replace = flaky  # type: ignore[assignment]
    try:
        write_durable(target, "one")
        assert target.read_text("utf-8") == "one"
        assert len(failures) == 2
        store = Store(owned(tree) / "control.json")
        os.replace = dead  # type: ignore[assignment]
        try:
            store.change(lambda _: Controls(context_caps={"a1": 5}))
        except PermissionError:
            pass
        else:
            raise AssertionError(UNREPORTED)
        assert store.controls == Controls()  # memory changes only after the file is written
        assert not (owned(tree) / "control.json").exists()
    finally:
        os.replace = real


def check_an_unreadable_control_file_stops_the_start(tree: Tree) -> None:
    path = owned(tree) / "control.json"
    bad = [
        "{not json",
        '{"paused": 5}',
        '{"paused": "s1"}',
        '{"context_caps": {"a1": NaN}, "paused": []}',
        '{"paused": [], "x": 1}',
    ]
    for text in bad:
        path.write_text(text, encoding="utf-8")
        try:
            Store(path)
        except SystemExit as stop:
            message = str(stop)
        else:
            raise AssertionError(text)
        assert "control.json" in message
        assert path.read_text("utf-8") == text  # never overwritten, never started with no caps


def check_a_refused_value_changes_neither_memory_nor_file(tree: Tree) -> None:
    store = Store(owned(tree) / "control.json")
    apply(store, {"action": "pause", "id": "s1"})
    before = (owned(tree) / "control.json").read_bytes()
    bad: list[dict[str, Any]] = [
        {"action": "budget", "scope": "daily", "value": 0},
        {"action": "budget", "scope": "default_agent", "value": -1},
        {"action": "budget", "scope": "daily", "value": "abc"},
        {"action": "budget", "scope": "daily", "value": True},
        {"action": "budget", "scope": "daily", "value": float("nan")},
        {"action": "context", "scope": "default_agent", "value": 2_000_000},
        {"action": "context", "scope": "row", "id": "s1", "value": 0},
        {"action": "budget", "scope": "row", "value": 5},
        {"action": "budget", "scope": "row", "id": "", "value": 5},
        {"action": "nope"},
        {"action": "budget", "scope": "elsewhere", "value": 5},
    ]
    for body in bad:
        try:
            apply(store, body)
        except ValueError:
            pass
        else:
            raise AssertionError(body)
        assert (owned(tree) / "control.json").read_bytes() == before, body
        assert store.controls.paused == frozenset({"s1"})
        assert store.controls.context_caps == {}


def check_valid_controls_apply_and_empty_clears_a_cap(tree: Tree) -> None:
    store = Store(owned(tree) / "control.json")
    sets: list[dict[str, Any]] = [  # a row's context cap is the one cap left
        {"action": "context", "scope": "row", "id": "a1", "value": 200_000},
        {"action": "context", "scope": "row", "id": "s1", "value": 300_000},
    ]
    for body in sets:
        apply(store, body)
    assert store.controls.context_caps == {"a1": 200_000, "s1": 300_000}
    apply(store, {**sets[0], "value": ""})
    assert store.controls.context_caps == {"s1": 300_000}
    apply(store, {**sets[1], "value": None})
    assert store.controls.context_caps == {}
    apply(store, {"action": "pause", "id": "s1"})
    apply(store, {"action": "resume", "id": "s1"})
    assert store.controls.paused == frozenset()
    assert Store(owned(tree) / "control.json").controls == store.controls


def check_old_control_keys_are_dropped_on_load(tree: Tree) -> None:
    """The six dollar and default keys load dropped, and the next write omits them."""
    path = owned(tree) / "control.json"
    old = dict.fromkeys(OLD_KEYS, 7) | {"budgets": {"a1": 2.5}}
    path.write_text(json.dumps({**old, "paused": ["s1"], "context_caps": {"a1": 9}}), "utf-8")
    store = Store(path)
    assert store.controls == Controls(paused=frozenset({"s1"}), context_caps={"a1": 9})
    apply(store, {"action": "pause", "id": "s2"})
    assert set(json.loads(path.read_text("utf-8"))) == {"paused", "context_caps"}


def check_a_dollar_control_is_refused(tree: Tree) -> None:
    """A dollar, default, turns or time control is refused naming it; nothing changes."""
    store = Store(owned(tree) / "control.json")
    apply(store, {"action": "context", "scope": "row", "id": "a1", "value": 200_000})
    before, controls = (owned(tree) / "control.json").read_bytes(), store.controls
    refused = [
        ({"action": "budget", "scope": "daily", "value": 50}, "budget"),
        ({"action": "budget", "scope": "row", "id": "a1", "value": 5}, "budget"),
        ({"action": "budget", "scope": "default_agent", "value": 5}, "budget"),
        ({"action": "turns", "scope": "row", "id": "a1", "value": 5}, "turns"),
        ({"action": "seconds", "scope": "row", "id": "a1", "value": 5}, "seconds"),
        ({"action": "context", "scope": "default_agent", "value": 500_000}, "default_agent"),
        ({"action": "context", "scope": "default_session", "value": 500_000}, "default_session"),
    ]
    for body, key in refused:
        try:
            apply(store, body)
        except ValueError as error:
            assert key in str(error), (body, str(error))
        else:
            raise AssertionError(body)
        assert (owned(tree) / "control.json").read_bytes() == before, body
        assert store.controls == controls, body


def check_the_settings_merge_keeps_every_key_and_refuses_a_bad_file(tree: Tree) -> None:
    path = owned(tree) / "settings.json"
    original: dict[str, Any] = {
        "model": "x",
        "hooks": {"Stop": [{"matcher": "*"}]},
        "env": {"A": "é"},
    }
    path.write_text(json.dumps(original), encoding="utf-8")
    merge_settings(path, lambda s: s.update(autoCompactWindow=400_000))
    assert json.loads(path.read_text("utf-8")) == {**original, "autoCompactWindow": 400_000}
    assert json.loads(path.with_name("settings.json.bak").read_text("utf-8")) == original
    path.write_text(json.dumps(original), encoding="utf-8")
    merge_settings(path, lambda s: s["hooks"].update(PreToolUse=[OLD_HOOK]))
    hooks = json.loads(path.read_text("utf-8"))["hooks"]
    assert len(hooks["PreToolUse"]) == 1
    assert hooks["Stop"] == original["hooks"]["Stop"]
    assert hooks["PreToolUse"][0]["hooks"][0]["type"] == "http"
    for text in ("{broken", "[1]"):
        path.write_text(text, encoding="utf-8")
        try:
            merge_settings(path, lambda s: s.update(model="y"))
        except ValueError as refused:
            message = str(refused)
        else:
            raise AssertionError(text)
        assert "not touching it" in message
        assert path.read_text("utf-8") == text  # refused, and left as it was


def check_a_settings_file_that_changed_meanwhile_is_refused_and_kept(tree: Tree) -> None:
    path = owned(tree) / "settings.json"
    path.write_text(json.dumps({"model": "x"}), encoding="utf-8")
    outside = {"model": "x", "permissions": {"allow": ["Bash"]}}

    def claude_code_writes(settings: dict[str, Any]) -> None:
        settings["autoCompactWindow"] = 400_000
        path.write_text(json.dumps(outside), encoding="utf-8")

    try:
        merge_settings(path, claude_code_writes)
    except RuntimeError as refused:
        message = str(refused)
    else:
        raise AssertionError(UNREPORTED)
    assert "changed while it was updated" in message
    assert json.loads(path.read_text("utf-8")) == outside  # the other write survives


def check_the_settings_view_never_reads_a_bad_file_as_not_wired(tree: Tree) -> None:
    path = owned(tree) / "settings.json"
    view = settings_view(path, URL)
    assert view == {"hook_wired": False, "settings_error": None}
    merge_settings(path, lambda s: s.update(hooks={"PreToolUse": [OLD_HOOK]}))
    assert settings_view(path, URL) == {"hook_wired": True, "settings_error": None}
    path.write_text("{broken", encoding="utf-8")
    view = settings_view(path, URL)
    assert view["hook_wired"] is None
    assert "does not parse" in view["settings_error"]


if __name__ == "__main__":
    checks: list[Callable[[Tree], None]] = [
        obj for name, obj in list(globals().items()) if name[:6] == "check_"
    ]
    run("dev-loop store", checks)
