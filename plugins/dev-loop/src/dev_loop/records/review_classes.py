"""The class table of the pitfalls file and how a lesson's label resolves to a row.

The pitfalls file is `knowledge.pitfalls` and the changes folder `paths.changes` of the
project's config. Split out of review_lessons.py. Reads files only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from dev_loop.config import load

# The default of `knowledge.pitfalls`, kept for review_rules.py until it reads `pitfalls_path`.
PITFALLS = Path("REVIEW-PITFALLS.md")
SECTION = re.compile(r"^## (\d+)\. .*?(?=^## |\Z)", re.MULTILINE | re.DOTALL)
FORMERLY = re.compile(r"^\*\*Formerly:\*\*\s*(.+)$", re.MULTILINE)
CLASS_ROW = re.compile(r"^\| (\d+) \| (.+?) \| .*? \| .*? \| (.+) \|$", re.MULTILINE)
FOLDED = re.compile(r"^(.*?)\s*\(folded (\d{4}-\d{2}-\d{2})\)$")


@dataclass(frozen=True)
class ClassRow:
    number: int
    text: str
    guard_cell: str
    former: tuple[str, ...] = ()  # titles before a sharpening: the section's **Formerly:** line
    folded: tuple[tuple[str, str], ...] = ()  # (name, "YYYY-MM-DD") of a dated `X (folded <date>)`


def former_of(line: str | None) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
    """Split a **Formerly:** line (`; `-separated) into names and its dated folded entries."""
    titles: list[str] = []
    dated: list[tuple[str, str]] = []
    for entry in (line or "").split(";"):
        match = FOLDED.match(entry.strip())
        name = match[1] if match else entry.strip()
        if name:
            titles.append(name)
        if match:
            dated.append((name, match[2]))
    return tuple(titles), tuple(dated)


def changes_dir(root: Path) -> Path:
    """Return the folder holding one folder per change: `paths.changes`, relative to root."""
    return Path(load(root).values["paths"]["changes"])


def pitfalls_path(root: Path) -> Path:
    """Return the pitfalls file: `knowledge.pitfalls`, relative to root."""
    return Path(load(root).values["knowledge"]["pitfalls"])


def class_rows(root: Path) -> list[ClassRow]:
    path = root / pitfalls_path(root)
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    former = {}
    for section in SECTION.finditer(text):
        line = FORMERLY.search(section[0])
        former[int(section[1])] = former_of(line[1] if line else None)
    return [
        ClassRow(int(n), t, g, *former.get(int(n), ((), ())))
        for n, t, g in CLASS_ROW.findall(text)
    ]


def normalised(text: str) -> str:
    return " ".join(text.lower().split())


def names(row: ClassRow, text: str) -> bool:
    """True when `text` is the row's title now or one of its former titles."""
    return normalised(text) in {normalised(t) for t in (row.text, *row.former)}


def resolve(label: str, rows: list[ClassRow]) -> str:
    """Return the class a label names: a row's text, "new: <name>", else "unclassified: <label>".

    `label` is a `Class:` value ("9 <row text>", "9", "new: x") or a legacy clause ("9. A failure
    swallowed", "A failure swallowed", "Races"). "9 <row text>" holds only while class 9 still
    reads that text or a former name (its section's **Formerly:** line: old titles and folded
    `new:` causes), so replacing a row never re-assigns the lessons logged under the old one;
    a bare number or a legacy "9." takes the row now holding it, else a case-insensitive prefix
    of a row's text does.
    """
    label = label.strip()
    if label.lower().startswith("new:"):
        name = label[4:].strip()
        row = next((r for r in rows if names(r, name)), None)  # folded into a row
        return row.text if row else f"new: {name}"
    by_number = {row.number: row.text for row in rows}
    stored = re.match(r"^(\d+) (\S.*)$", label)
    if stored:
        row = next((r for r in rows if r.number == int(stored[1])), None)
        if row and names(row, stored[2]):
            return row.text
        return f"unclassified: {stored[2]}"
    number = re.match(r"^(?:class\s+)?(\d+)(?:[.,]\s|$)", label, re.IGNORECASE)
    if number and int(number[1]) in by_number:
        return by_number[int(number[1])]
    clause = re.sub(r"^\d+\.\s*", "", label)
    for row in rows:
        if clause and any(
            normalised(t).startswith(normalised(clause)) for t in (row.text, *row.former)
        ):
            return row.text
    return f"unclassified: {clause}"


def rowed(name: str, rows: list[ClassRow]) -> bool:
    """A `new: <name>` class is rowed once a row's whole text, or a former title, is that name."""
    label = name.partition(": ")[2]
    return name.startswith("new:") and any(names(row, label) for row in rows)
