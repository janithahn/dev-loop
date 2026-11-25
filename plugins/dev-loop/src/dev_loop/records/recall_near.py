"""Recall's reading of run-together lessons and its relevance floor (v0.4.19)."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from . import skipped as skipped_lines

# Another field's label inside a field: a legacy lesson whose migration ran two lessons together
# (an Avoid that holds a second lesson's Summary and Class).
GARBLED = re.compile(
    r"(?:^|\s)(?:Summary|Avoid|Class|Guard|Files|Round):\s|(?:^|\n)#{2,} |#{2,} \[L\d+\]")
AVOID = re.compile(r"(?:^|\s)Avoid:\s")
SEPARATORS = " \t\n.;:,-·—"


def before_label(text: str) -> str:
    """Return the text before its first embedded label, without a trailing separator."""
    found = GARBLED.search(text)
    return text[: found.start()].rstrip(SEPARATORS) if found else text


def cut(lesson: dict[str, Any]) -> dict[str, Any]:
    """Return the lesson, or a copy marked `cut` with summary and avoid cut at an embedded label.

    An empty avoid takes the summary's embedded `Avoid:` text, up to the next label.
    """
    summary, avoid = lesson.get("summary") or "", lesson.get("avoid") or ""
    if not GARBLED.search(summary) and not GARBLED.search(avoid):
        return lesson
    embedded = AVOID.search(summary)
    if not avoid and embedded:
        avoid = summary[embedded.end() :]
    return lesson | {"summary": before_label(summary), "avoid": before_label(avoid), "cut": True}


def read_lessons(
    root: Path, found: list[tuple[str, dict[str, Any]]], bad: int
) -> tuple[list[tuple[str, dict[str, Any]]], list[str]]:
    """Return the lessons, run-together ones cut, and a line per kind skipped (bad, empty)."""
    read = [(c, cut(r)) for c, r in found if r["kind"] == "lesson"]
    lessons = [(c, r) for c, r in read if r.get("summary") or "cut" not in r]
    mixed = len(read) - len(lessons)
    skipped = [f"{bad} bad sidecar lines skipped (dl records unparsed)"] if bad else []
    if bad:
        skipped_lines.note(root)
    skipped += [f"{mixed} lessons skipped: their fields ran together"] if mixed else []
    return lessons, skipped


def near_enough(share: float, score: float, count: int, placed: bool) -> bool:
    """Return whether a ranked lesson is near: it shares a place, or its words clear the floor.

    The floor is ln(count), the idf of a word only that lesson has; twice that for a lesson off
    the places the paths name. A lesson sharing no word (score 0) is never near by words.
    """
    floor = math.log(count) * (2 if placed else 1)
    return share > 0 or (score > 0 and score >= floor)
