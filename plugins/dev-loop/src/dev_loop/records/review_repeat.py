"""`dl records repeat`: the repeat-mistake rate, old and new side by side, with the counts.

Old: a round-1 lesson repeats when its class has a row in the pitfalls file (`is_repeat`).
New: it repeats only when the mistake could have been avoided. Either the builder was shown a
lesson of its class (a `recall` record of the change naming one), or a guard path in the class's
row covers one of its files and landed on `origin/<branches.integration>` before the lesson.
Lessons two review passes logged for one problem (same change, class and files) count once.
A lesson nothing decides (no recall record, no guard) is unknown, and a week with one reads
not_run, never a rate. The changes folder is `paths.changes`, the pitfalls file
`knowledge.pitfalls`. Reads files and git only.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path

from ..config import load
from . import record
from .review_classes import ClassRow, class_rows
from .review_evaluate import first_commit
from .review_guards import BACKTICKED
from .review_lessons import Lesson, is_repeat, parse_lessons, week_of

HEADER = (
    "| Week | Round-1 lessons | Old: class exists | Round-1 findings (two passes once) "
    "| New: shown to the builder or guarded |\n|---|---|---|---|---|"
)
TESTS = ("tests", "test")


def covers(guard: str, name: str) -> bool:
    """True when the guard path reads the file: a guard reads its own folder, and one inside a
    tests folder reads the folder that holds the tests (the whole tree for a top-level one)."""
    parts = guard.split("/")[:-1]
    cut = next((i for i, part in enumerate(parts) if part in TESTS), len(parts))
    scope = "/".join(parts[:cut])
    return not scope or name.startswith(f"{scope}/")


def shown_classes(root: Path, lessons: list[Lesson]) -> dict[str, set[str]]:
    """Return, per change with a recall record, the classes of the lessons shown to its builder."""
    class_of = {f"{x.change}:{x.id}": x.class_ for x in lessons}
    shown: dict[str, set[str]] = defaultdict(set)
    for folder in record.folders(root):
        for made in record.read(folder):
            if made["kind"] == "recall":
                ids = str(made.get("shown", "")).split()
                shown[str(made["change"])] |= {class_of[i] for i in ids if i in class_of}
    return shown


class Judge:
    """The new definition, with each guard's landing on the integration branch looked up once."""

    def __init__(self, root: Path, rows: list[ClassRow], lessons: list[Lesson]) -> None:
        self.root, self.rows = root, rows
        self.shown = shown_classes(root, lessons)
        loaded = load(root)
        self.ref = f"{loaded.get('branches.remote')}/{loaded.get('branches.integration')}"
        self.landed: dict[str, datetime | None] = {}

    def guarded(self, lesson: Lesson) -> bool:
        cell = next((r.guard_cell for r in self.rows if r.text == lesson.class_), "")
        for guard in (path for path in BACKTICKED.findall(cell) if "/" in path):
            if guard not in self.landed:
                self.landed[guard] = first_commit(self.root, guard, self.ref)
            since = self.landed[guard]
            early = since is not None and lesson.when is not None and since < lesson.when
            if early and any(covers(guard, name) for name in lesson.files):
                return True
        return False

    def repeat(self, lesson: Lesson) -> bool | None:
        """True, False, or None when nothing recorded decides it."""
        if not is_repeat(lesson, self.rows):
            return False  # a new or unclassified class repeats under neither definition
        if lesson.class_ in self.shown.get(lesson.change, set()) or self.guarded(lesson):
            return True
        return False if lesson.change in self.shown else None


def once(lessons: list[Lesson]) -> list[Lesson]:
    """Keep one lesson per problem: two passes' lessons share change, class and files."""
    kept = {(x.change, x.class_, tuple(sorted(x.files))): x for x in reversed(lessons)}
    return list(reversed(kept.values()))


def share(verdicts: list[bool | None]) -> str:
    unknown = verdicts.count(None)
    if not verdicts:
        return "not_run"
    if unknown:
        return f"not_run ({unknown} of {len(verdicts)} unknown)"
    hits = sum(map(bool, verdicts))
    return f"{hits} of {len(verdicts)} ({hits / len(verdicts):.0%})"


def rates(root: Path) -> list[str]:
    """Return per ISO week, newest first, the round-1 lessons and both rates with their counts."""
    rows = class_rows(root)
    lessons = parse_lessons(root, rows)
    judge = Judge(root, rows, lessons)
    weeks: dict[str, list[Lesson]] = defaultdict(list)
    for lesson in lessons:
        if lesson.when and lesson.round == 1:
            weeks[week_of(lesson)].append(lesson)
    table = HEADER.splitlines()
    for week in sorted(weeks, reverse=True):
        first, findings = weeks[week], once(weeks[week])
        old = share([is_repeat(x, rows) for x in first])
        new = share([judge.repeat(x) for x in findings])
        table.append(f"| {week} | {len(first)} | {old} | {len(findings)} | {new} |")
    return table
