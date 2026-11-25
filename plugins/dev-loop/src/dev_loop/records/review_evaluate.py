"""`dl records evaluate`: do the rules and guards stop repeats? (lessons-auto-load)

Per ISO week, newest first: the round-1 repeat rate overall (a repeat as `trend` counts it) and
per class (that class's round-1 repeats over the week's round-1 lessons). Per guard file in
the pitfalls file's table: the round-1 lessons logged in its class since the guard's first
commit; a guard with `WEAK` or more is weak (its class keeps recurring past it). Every lesson
records a fixed blocker or major. Without git a guard's start reads "—" and counts every lesson.
"""

from __future__ import annotations

import subprocess
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from .review_classes import ClassRow, class_rows
from .review_guards import BACKTICKED
from .review_lessons import Lesson, is_repeat, parse_lessons, week_of

WEAK = 3
WEEK_HEADER = (
    "| Week | Round-1 lessons | Round-1 repeats | Rate | By class (round-1 repeats, share) |\n"
    "|---|---|---|---|---|"
)
GUARD_HEADER = "| Class | Guard | Since | Round-1 lessons since | Verdict |\n|---|---|---|---|---|"


def first_commit(root: Path, path: str, ref: str = "HEAD") -> datetime | None:
    """Return when `path` was first added on `ref` (following renames), else None (no git, no
    history, no such ref)."""
    try:
        log = subprocess.run(  # noqa: S603, S607 - fixed git arguments
            ["git", "log", "--follow", "--diff-filter=A", "--format=%aI", ref, "--", path],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        ).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        return None
    return datetime.fromisoformat(log[-1]) if log else None


def weeks(lessons: list[Lesson], rows: list[ClassRow]) -> list[str]:
    """Return a row per week: round-1 lessons, repeats, the rate, and each class's share."""
    number = {r.text: r.number for r in rows}
    by_week: dict[str, list[Lesson]] = defaultdict(list)
    for lesson in lessons:
        if lesson.when and lesson.round == 1:
            by_week[week_of(lesson)].append(lesson)
    table = [WEEK_HEADER]
    for week in sorted(by_week, reverse=True):
        first = by_week[week]
        repeats = [x for x in first if is_repeat(x, rows)]
        per = Counter(number[x.class_] for x in repeats)
        classes = " · ".join(f"{n}: {k} ({k / len(first):.0%})" for n, k in per.most_common())
        rate = f"{len(repeats) / len(first):.0%}"
        table.append(f"| {week} | {len(first)} | {len(repeats)} | {rate} | {classes or '—'} |")
    return table


def guards(root: Path, lessons: list[Lesson], rows: list[ClassRow]) -> list[str]:
    """Return a row per guard file: its class's round-1 lessons since it landed, weak or holding."""
    table = [GUARD_HEADER]
    for row in rows:
        for path in [t for t in BACKTICKED.findall(row.guard_cell) if "/" in t]:
            since = first_commit(root, path)
            after = [
                x
                for x in lessons
                if x.class_ == row.text
                and x.round == 1
                and x.when
                and (since is None or x.when >= since)
            ]
            verdict = "weak" if len(after) >= WEAK else "holding"
            start = since.date().isoformat() if since else "—"
            table.append(f"| {row.number} | `{path}` | {start} | {len(after)} | {verdict} |")
    return table


def evaluate(root: Path) -> list[str]:
    rows = class_rows(root)
    lessons = parse_lessons(root, rows)
    return [*weeks(lessons, rows), "", *guards(root, lessons, rows)]
