"""Lessons for review_records.py: parse, class resolution, the pitfalls doc's table, current, trend.

A lesson is a `## [Ln]` block in a change's `learnings.md`. A current lesson's Detail carries
`Summary:`, `Class:`, `Guard:`, `Files:` and `Round:` lines; an older lesson carries only a
Summary, so `class_` falls back to the Summary's first clause and `files` and `round` stay empty.
Reads files only.
"""

from __future__ import annotations

import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import incident, review_guards
from .review_classes import (
    ClassRow,
    changes_dir,
    class_rows,
    normalised,
    pitfalls_path,
    resolve,
    rowed,
)

UTC = timezone.utc  # datetime.UTC is 3.11+
NEVER = datetime.min.replace(tzinfo=UTC)  # sorts a lesson with no date first

BLOCK = re.compile(r"^## \[(L\d+)\]", re.MULTILINE)
ACTION = re.compile(r"^### Action\s+(?:Avoid:\s*)?(.+)", re.MULTILINE)
SUMMARY = re.compile(r"^(?:\*\*Summary:\*\*|Summary:)\s*(.+)", re.MULTILINE)
WHEN = re.compile(r"^\*\*When:\*\*\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2})", re.MULTILINE)
MAX_CLASSES = 15
TREND_HEADER = (
    "| Week | Lessons (blocker+major fixed) | Repeats (round 1) | Repeats (all rounds) "
    "| New-class lessons | Repeat rate (round 1) |\n|---|---|---|---|---|---|"
)


@dataclass(frozen=True)
class Lesson:
    change: str
    id: str
    when: datetime | None
    summary: str
    class_: str  # a class row's text, "new: <name>" or "unclassified: <clause>"
    guard: str | None
    files: tuple[str, ...]
    round: int | None
    label: str = ""  # the stated `Class:` or first clause, before it resolves to a row
    avoid: str = ""  # the Action's first sentence
    check: str = ""  # the Action's Self-check


def first_clause(summary: str) -> str:
    """Return the Summary's first clause: before the first colon, "new: <name>" kept whole."""
    parts = [part.strip() for part in summary.split(":")]
    name = f"new: {parts[1]}" if parts[0].lower() == "new" and len(parts) > 1 else parts[0]
    return re.sub(r"\s*\(.*\)$", "", name)


def field(block: str, name: str) -> str | None:
    """Return a `Name:` line of the Detail only: the writer's header copies its first characters."""
    match = re.search(rf"^{name}:\s*(.+)$", block.partition("### Detail")[2], re.MULTILINE)
    return match[1].strip() if match else None


def learnings_texts(root: Path) -> dict[str, str]:
    """Return each change's learnings.md text, keyed by its repo-relative path."""
    found = (root / changes_dir(root)).rglob("learnings.md")
    return {p.relative_to(root).as_posix(): p.read_text(encoding="utf-8") for p in sorted(found)}


def learnings_texts_at(root: Path, base: str) -> dict[str, str]:
    """Return the same map as it stood at the git ref `base` (empty where the tree has none)."""

    def git(*args: str) -> str:
        return subprocess.run(  # noqa: S603, S607 - fixed git arguments
            ["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8", check=True
        ).stdout

    where = changes_dir(root).as_posix()
    listed = git("ls-tree", "-r", "--name-only", base, "--", where).splitlines()
    return {n: git("show", f"{base}:{n}") for n in listed if n.endswith("/learnings.md")}


def avoid_of(block: str) -> str:
    """Return the Action's first sentence ("Avoid: " dropped), cut before its Self-check."""
    action = ACTION.search(block)
    return re.split(r"\.\s+Self-check", action[1])[0].strip().rstrip(".") if action else ""


def check_of(block: str) -> str:
    """Return the Action's Self-check sentence, else empty."""
    action = ACTION.search(block)
    found = re.search(r"Self-check:\s*(.+)", action[1]) if action else None
    return found[1].strip() if found else ""


def make_lesson(name: str, block: str, lesson_id: str, rows: list[ClassRow]) -> Lesson | None:
    summary = SUMMARY.search(block)
    if not summary:
        return None
    stated, round_ = field(block, "Class"), field(block, "Round")
    when = WHEN.search(block)
    label = stated or first_clause(summary[1])
    return Lesson(
        change=Path(name).parent.name,
        id=lesson_id,
        when=datetime.strptime(when[1], "%Y-%m-%d %H:%M").replace(tzinfo=UTC) if when else None,
        summary=summary[1],
        class_=resolve(label, rows),
        guard=field(block, "Guard"),
        files=tuple((field(block, "Files") or "").split()),
        round=int(round_) if round_ and round_.isdigit() else None,
        label=label,
        avoid=avoid_of(block),
        check=check_of(block),
    )


def parse_lessons(
    root: Path, rows: list[ClassRow], texts: dict[str, str] | None = None
) -> list[Lesson]:
    lessons = []
    for name, text in (learnings_texts(root) if texts is None else texts).items():
        starts = [(m.start(), m[1]) for m in BLOCK.finditer(text)]
        ends = [s for s, _ in starts[1:]] + [len(text)]
        for (start, lesson_id), end in zip(starts, ends, strict=True):
            made = make_lesson(name, text[start:end], lesson_id, rows)
            if made:
                lessons.append(made)
    return lessons


def class_guard(name: str, rows: list[ClassRow], lessons: list[Lesson]) -> str:
    """Return the doc's guard cell for a class row; for a new class its most recent `Guard:`."""
    for row in rows:
        if row.text == name:
            return row.guard_cell
    stated = [x for x in lessons if x.class_ == name and x.guard]
    if not stated:
        return "—"
    return str(max(stated, key=lambda x: x.when or NEVER).guard)


def changes_by_class(lessons: list[Lesson]) -> dict[str, set[str]]:
    changes: dict[str, set[str]] = defaultdict(set)
    for lesson in lessons:
        changes[lesson.class_].add(lesson.change)
    return changes


def lesson_counts(root: Path) -> list[tuple[int, int, str, str]]:
    """Return (changes, lessons, class, guard) per class, most changes first; incidents too."""
    rows = class_rows(root)
    lessons = parse_lessons(root, rows)
    changes = changes_by_class(lessons)
    totals: dict[str, int] = defaultdict(int)
    for lesson in lessons:
        totals[lesson.class_] += 1
    counted = [
        (len(where), totals[name], name, class_guard(name, rows, lessons))
        for name, where in changes.items()
    ]
    return sorted(counted + incident.counts(root), reverse=True)


def unrowed_faults(
    rows: list[ClassRow], lessons: list[Lesson], added: set[str] | None, pitfalls: Path
) -> list[str]:
    """Rule 1: an unrowed class in 3+ changes; with `added`, only a class this tree adds to."""
    faults = []
    for name, logged in sorted(
        changes_by_class(lessons).items(), key=lambda item: (-len(item[1]), item[0])
    ):
        unrowed = name.startswith(("new:", "unclassified:")) and not rowed(name, rows)
        if len(logged) >= 3 and unrowed and (added is None or name in added):  # noqa: PLR2004
            faults.append(
                f"class {name!r} is in {len(logged)} changes ({', '.join(sorted(logged))}) with "
                f"no row in {pitfalls.as_posix()}: add a row (replace or sharpen one; at most 15 "
                "classes)"
            )
    return faults


def moved(root: Path) -> list[str]:
    """Verbose aid: each gone Guard a guard_moves key satisfies (`review_guards.moved`)."""
    rows = class_rows(root)
    return review_guards.moved(root, rows, parse_lessons(root, rows))


def added_classes(root: Path, rows: list[ClassRow], lessons: list[Lesson], base: str) -> set[str]:
    """Return the classes of the lessons present here and absent at the git ref `base`."""
    before = parse_lessons(root, rows, learnings_texts_at(root, base))
    seen = {(x.change, x.id, x.summary) for x in before}
    return {x.class_ for x in lessons if (x.change, x.id, x.summary) not in seen}


def current(root: Path, base: str | None = None, *, rule_one: bool = True) -> list[str]:
    """Return the doc's faults: an unrowed class in 3+ changes, a bad guard cell, a stale Guard.

    With `base` (a git ref), rule 1 fails only for a class to which this tree adds a lesson, so
    two PRs that each add to one class never fail a later PR that added nothing to it. With
    `rule_one` False (a checkout with no git, such as the dev container) rule 1 is left to the
    pr-gates step that runs it on the agent against the target branch.
    """
    pitfalls = pitfalls_path(root)
    rows = class_rows(root)
    lessons = parse_lessons(root, rows)
    added = None if base is None else added_classes(root, rows, lessons, base)
    faults = []
    if not 1 <= len(rows) <= MAX_CLASSES:
        faults.append(
            f"{pitfalls.as_posix()} has {len(rows)} class rows (no class rows, or more than "
            f"{MAX_CLASSES}): the table must hold 1 to {MAX_CLASSES} classes"
        )
    unrowed = unrowed_faults(rows, lessons, added, pitfalls) if rule_one else []
    return faults + unrowed + review_guards.faults(root, rows, lessons)


def iso_week(when: datetime) -> str:
    year, week, _ = when.isocalendar()
    return f"{year}-W{week:02d}"


def week_of(lesson: Lesson) -> str:
    return iso_week(lesson.when or NEVER)


def before_fold(lesson: Lesson, rows: list[ClassRow]) -> bool:
    """True for a `new: X` lesson whose X was folded into a row, dated after the lesson."""
    if not lesson.label.lower().startswith("new:") or lesson.class_.startswith("new:"):
        return False
    name = lesson.label[4:].strip()
    row = next((r for r in rows if r.text == lesson.class_), None)
    dated = [d for n, d in row.folded if normalised(n) == normalised(name)] if row else []
    return bool(dated and lesson.when and lesson.when.date().isoformat() <= max(dated))


def is_new(lesson: Lesson, rows: list[ClassRow]) -> bool:
    return lesson.class_.startswith("new:") or before_fold(lesson, rows)


def is_repeat(lesson: Lesson, rows: list[ClassRow]) -> bool:
    """A lesson in a known class: neither new (nor before its fold) nor unclassified."""
    return not is_new(lesson, rows) and not lesson.class_.startswith("unclassified:")


def trend(root: Path) -> list[str]:
    """Return the Trend table: per ISO week, newest first, round 1 apart from all rounds.

    A repeat is judged against today's class table, except that a `new:` lesson written on or
    before the date its cause was folded into a row (`X (folded <date>)`) stays new.
    """
    rows = class_rows(root)
    weeks: dict[str, list[Lesson]] = defaultdict(list)
    for lesson in parse_lessons(root, rows):
        if lesson.when:
            weeks[week_of(lesson)].append(lesson)
    table = [TREND_HEADER]
    for week in sorted(weeks, reverse=True):
        made = weeks[week]
        new = [x for x in made if is_new(x, rows)]
        repeats = [x for x in made if is_repeat(x, rows)]
        stated = [x for x in made if x.round is not None]
        first = [x for x in repeats if x.round == 1]
        round_one = len([x for x in stated if x.round == 1])
        rate = f"{len(first) / round_one:.0%}" if round_one else "—"
        table.append(
            f"| {week} | {len(made)} | {len(first) if stated else '—'} | {len(repeats)} "
            f"| {len(new)} | {rate} |"
        )
    return table + incident.trend(root)  # the loop's own incidents, a table of their own
