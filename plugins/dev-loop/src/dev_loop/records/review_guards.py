"""Guard checks for the records-current check: the table's guard cells and lessons' `Guard:` (G5).

A lesson's `Guard:` names the test or hook that proves its class cannot recur. A project that
moves its loop onto the plugin deletes its own copies, so a historical lesson's Guard path goes
missing; the `records.guard_moves` config maps an old guard path or glob to its new home, so such a
lesson counts as satisfied, not stale. History (learnings.md, records.jsonl) is never rewritten.
Reads files and the config only.
"""

from __future__ import annotations

import os
import re
from fnmatch import fnmatchcase
from pathlib import Path
from typing import TYPE_CHECKING

from .. import config

if TYPE_CHECKING:
    from .review_classes import ClassRow
    from .review_lessons import Lesson

BACKTICKED = re.compile(r"`([^`]+)`")


def plugin_root() -> Path:
    """The installed plugin's root: CLAUDE_PLUGIN_ROOT, else this file's plugin folder."""
    return Path(os.environ.get("CLAUDE_PLUGIN_ROOT") or Path(__file__).resolve().parents[3])


def guard_moves(root: Path) -> dict[str, str]:
    """The records.guard_moves map from the project config (empty when unset or on a bad file)."""
    return dict(config.load(root).get("records.guard_moves"))


def gone(guard: str, root: Path) -> bool:
    """True if a stated Guard path no longer exists (empty, `open`, `review:` are not paths)."""
    stated = guard not in ("", "open") and not guard.startswith("review:")
    return stated and not (root / guard).exists()


def move_target(guard: str, moves: dict[str, str]) -> str | None:
    """The guard_moves value whose key matches `guard` (exact, or as a glob), else None."""
    return next((value for key, value in moves.items() if fnmatchcase(guard, key)), None)


def present(value: str, root: Path, plugin: Path) -> bool:
    """True if a move target is present: a `plugin:<name>` test or hook, else a project path."""
    name = value.partition("plugin:")[2] if value.startswith("plugin:") else ""
    if value.startswith("plugin:"):
        return bool(name) and any(list((plugin / d).rglob(name)) for d in ("tests", "hooks"))
    return (root / value).exists()


def moved_to(guard: str, root: Path, moves: dict[str, str], plugin: Path) -> str | None:
    """The move value that satisfies a gone Guard, else None (unmapped, or the target absent)."""
    value = move_target(guard, moves)
    return value if value is not None and present(value, root, plugin) else None


def cell_fault(row: ClassRow, root: Path) -> str | None:
    """Rule 2: the class table's guard cell needs a backticked path that exists, or a reason."""
    paths = [t for t in BACKTICKED.findall(row.guard_cell) if "/" in t]
    missing = [p for p in paths if not (root / p).exists()]
    reasoned = re.match(r"^review only:\s*\S", row.guard_cell)
    if missing or not (paths or reasoned):
        return (
            f"class {row.number} guard cell: needs a backticked path that exists "
            f"({', '.join(missing) or 'none given'}) or 'review only: <reason>'"
        )
    return None


def faults(root: Path, rows: list[ClassRow], lessons: list[Lesson]) -> list[str]:
    """Rules 2 and 3: a bad guard cell in the table, a stale `Guard:` in a lesson.

    A Guard whose path is gone but whose path or glob is a guard_moves key counts as satisfied,
    not stale, when its target exists (a project path, or a plugin test or hook).
    """
    moves, plugin = guard_moves(root), plugin_root()
    found = [cell_fault(row, root) for row in rows]
    for lesson in lessons:
        guard = lesson.guard or ""
        if gone(guard, root) and moved_to(guard, root, moves, plugin) is None:
            found.append(f"{lesson.change} {lesson.id}: Guard: {guard} no longer exists; "
                         "update the lesson")
    return [f for f in found if f]


def moved(root: Path, rows: list[ClassRow], lessons: list[Lesson]) -> list[str]:
    """Verbose aid: each gone Guard a guard_moves key satisfies, as `<old> moved -> <new>`."""
    moves, plugin = guard_moves(root), plugin_root()
    notes = []
    for lesson in lessons:
        guard = lesson.guard or ""
        value = moved_to(guard, root, moves, plugin) if gone(guard, root) else None
        if value is not None:
            notes.append(f"{lesson.change} {lesson.id}: Guard: {guard} moved -> {value}")
    return notes
