"""The owner's controls and the settings merge, written durably."""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from .gate import Controls

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

OLD_KEYS = ("daily_usd", "session_usd", "agent_usd", "budgets", "session_context", "agent_context")
SETTINGS_LOCK = threading.Lock()


def write_durable(path: Path, text: str, guard: Callable[[], None] | None = None) -> None:
    """Write temp, flush, fsync, run `guard`, replace; a reader holding the file is retried."""
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:  # the text, byte for byte
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    if guard:
        guard()  # raises to refuse the replace: the file changed since it was read
    for attempt in range(5):
        try:
            tmp.replace(path)
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.05)
        else:
            return


def as_dict(controls: Controls) -> dict[str, Any]:
    """Return what control.json holds: the pauses and the per-agent context caps."""
    return {"paused": sorted(controls.paused), "context_caps": dict(controls.context_caps)}


def edit(controls: Controls, body: dict[str, Any]) -> Controls:
    """Return the controls the request asks for: pause, resume, or one row's context cap.

    Anything else (a dollar, turns, time or default cap) raises ValueError naming it.
    """
    action, scope, ident, value = (body.get(k) for k in ("action", "scope", "id", "value"))
    if action not in ("pause", "resume", "context"):
        msg = f"{action!r} is not a control: only pause, resume and context (scope row)"
        raise ValueError(msg)
    if action == "context" and scope != "row":
        msg = f"context scope {scope!r} is not a control: only scope row"
        raise ValueError(msg)
    if not (isinstance(ident, str) and ident):
        msg = "an id is needed"
        raise ValueError(msg)
    if action == "context":  # empty clears the cap; Controls refuses 0 and the rest
        caps = {k: v for k, v in controls.context_caps.items() if k != ident}
        caps = caps if value in ("", None) else {**caps, ident: value}
        return replace(controls, context_caps=caps)
    paused = controls.paused | {ident} if action == "pause" else controls.paused - {ident}
    return replace(controls, paused=paused)


class Store:
    """control.json: memory is the truth and changes only after the file is written."""

    def __init__(self, path: Path) -> None:
        self.path, self.lock = path, threading.Lock()
        self.controls = self.load()

    def load(self) -> Controls:
        """Return the stored controls; a file that is not valid stops the start, never no caps."""
        if not self.path.exists():
            return Controls()
        try:
            data = json.loads(self.path.read_text("utf-8"))
            if not isinstance(data["paused"], list):  # a string would load as one id per letter
                msg = "paused is not a list"
                raise TypeError(msg)  # noqa: TRY301 - stops the start below
            kept = {k: v for k, v in data.items() if k not in OLD_KEYS}  # dropped
            return Controls(**{**kept, "paused": frozenset(data["paused"])})
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
            msg = f"{self.path} is not valid ({type(error).__name__}: {error}); fix or delete it"
            raise SystemExit(msg) from error

    def change(self, make: Callable[[Controls], Controls]) -> Controls:
        with self.lock:  # one read-modify-write at a time: no lost update
            changed = make(self.controls)
            write_durable(self.path, json.dumps(as_dict(changed), allow_nan=False))
            self.controls = changed
            return changed


def read_settings(path: Path) -> tuple[str, dict[str, Any]]:
    """Return the file's text and its settings; ValueError when it does not parse as an object."""
    raw = path.read_text("utf-8") if path.exists() else "{}"
    try:
        settings = json.loads(raw)
        settings.keys()  # a list, a number or a string is not settings
    except (ValueError, AttributeError) as error:
        msg = f"{path} does not parse as an object; not touching it"
        raise ValueError(msg) from error
    return raw, dict(settings)


def merge_settings(path: Path, change: Callable[[dict[str, Any]], None]) -> None:
    """Merge into the settings: one read, its text kept as the .bak, a changed file refused."""
    with SETTINGS_LOCK:
        raw, settings = read_settings(path)
        change(settings)

        def unchanged() -> None:
            if read_settings(path)[0] != raw:
                msg = f"{path} changed while it was updated; nothing written, try again"
                raise RuntimeError(msg)

        if path.exists():
            write_durable(path.with_name(path.name + ".bak"), raw)
        text = json.dumps(settings, indent=2, ensure_ascii=False) + "\n"
        write_durable(path, text, unchanged)


def settings_view(path: Path, url: str) -> dict[str, Any]:
    """Return whether a PreToolUse hook names url; a settings file that does not parse is shown."""
    try:
        settings = read_settings(path)[1]
    except ValueError as problem:  # never read as "not wired"
        return {"hook_wired": None, "settings_error": str(problem)}
    hooks = settings.get("hooks")
    pre = hooks.get("PreToolUse") if isinstance(hooks, dict) else None
    return {"hook_wired": url in json.dumps(pre), "settings_error": None}
