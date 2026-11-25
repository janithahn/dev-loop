"""Log one review lesson into a change's learnings.md and records.jsonl, through the one writer.

    dl lesson <change> <high|medium|low> "Summary: <root cause>" \\
      "Avoid: <how to write it right>. Self-check: <the quick check>." \\
      "Class: <a row number of knowledge.pitfalls | new: <short name>>" \\
      "Guard: <a test path | review: <reason> | open>" \\
      "Files: <project paths, space- or comma-separated>" "Round: <the round that filed it, >= 1>"

A bad lesson is refused (exit 1, nothing written), so each carries the class the readers count.
On the local host a lesson is refused before the change's first clean review run.
A row number is stored with its row's text: replacing the row never re-assigns the lesson. No
customer data, party names or document text in a lesson: ids, rules and code only. A field that
quotes more than 40 characters (between straight or typographic double quotes, or a backtick span
with whitespace in it) is refused: say it in your own words. A one-word path or test id passes.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from ..config import project_root
from . import record, review_classes, review_rules, round_guard

FIELDS = ("Summary", "Avoid", "Class", "Guard", "Files", "Round")
USAGE = 'usage: dl lesson <change> <high|medium|low> "Summary: ..." "Avoid: ..." ' + " ".join(
    f'"{name}: ..."' for name in FIELDS[2:])


def fields(given: list[str]) -> dict[str, str]:
    """Return each `Name: value` argument's value, in FIELDS order; refuse a missing one."""
    if len(given) != len(FIELDS) or any(
            not text.startswith(f"{name}:") for name, text in zip(FIELDS, given, strict=True)):
        raise ValueError("a lesson needs Summary:, Avoid:, Class:, Guard:, Files: and Round:")
    return {name.lower(): text[len(name) + 1:].strip() for name, text in zip(FIELDS, given,
                                                                              strict=True)}


def resolved_class(root: Path, label: str) -> str:
    """Return a row number with its row's text, or `new: <name>` as given."""
    if re.fullmatch(r"new: ?\S.*", label):
        return label
    rows = {row.number: row.text for row in review_classes.class_rows(root)}
    if not label.isdigit() or int(label) not in rows:
        where = review_classes.pitfalls_path(root).as_posix()
        raise ValueError(f"Class: must be a row number of {where} or new: <name>")
    return f"{label} {rows[int(label)]}"


def checked(root: Path, lesson: dict[str, str]) -> dict[str, str]:
    """Refuse a Guard, Files or Round that does not hold; return the record's fields."""
    guard = lesson["guard"]
    if guard != "open" and not re.fullmatch(r"review: ?\S.*", guard):
        if "/" not in guard or not (root / guard).exists():
            raise ValueError("Guard: must be an existing path, 'review: <reason>' or open")
    paths = [path for path in re.split(r"[\s,]+", lesson["files"]) if path]
    missing = [path for path in paths if not (root / path).exists()]  # a glob is not a path
    if not paths or missing:
        found = f"Files: path not found: {' '.join(missing)}".rstrip(": ")
        raise ValueError(f"{found} (paths separated by spaces or commas)")
    if not re.fullmatch(r"[1-9]\d*", lesson["round"]):
        raise ValueError("Round: must be an integer >= 1")
    return lesson | {"class": resolved_class(root, lesson["class"]), "files": " ".join(paths)}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(USAGE, file=sys.stderr)
        return 2
    change, priority, *given = argv
    try:
        if priority not in ("high", "medium", "low"):
            raise ValueError("priority must be high, medium or low")
        root = project_root()
        pairs = [f"{key}={value}" for key, value in checked(root, fields(given)).items()]
        asked = record.parse("lesson", [f"priority={priority}", *pairs])
        written = record.write(root, change, round_guard.checked(change, asked))
    except ValueError as error:
        print(f"dl lesson: refused: {error}", file=sys.stderr)
        return 1
    print(written["line"])
    # Refresh the area guides' "Do not repeat" blocks; commit them with the lesson.
    # Only the guides of the lesson's own areas: the rest stay as they are on every branch.
    try:
        refreshed = review_rules.generate(root)
        if refreshed is None:
            names = review_rules.write(root, written["files"].split())
            print(chr(10).join(f"wrote {n}" for n in names))
            refreshed = 0
    except ValueError as error:
        print(f"dl records rules: {error}", file=sys.stderr)
        refreshed = 1
    if refreshed:
        print("dl lesson: run dl records rules --write", file=sys.stderr)
    return 0
