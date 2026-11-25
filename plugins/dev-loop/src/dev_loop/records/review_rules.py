"""`review_records.py rules`: the "Do not repeat" block in each area guide (lessons-auto-load).

Claude Code loads a folder's `CLAUDE.md` when an agent works in it, so each area guide
(`knowledge.guides`) carries, between `START` and `END`, the classes most logged against that
area (the rule, the guard, a Don't/Do snippet from the pitfalls file) and its newest concrete lessons. The builder sees them
without running `pitfalls` by hand.

The block must come out the same on every machine, since a test compares it: only lessons with
`Files:` are placed (the shape since 2026-10-09; placing an older lesson needs git history, which
the dev container lacks), and each area's four-week window ends at its newest lesson, not today.
With `--base`, only the guides a tree changes are judged (`touched`).
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from ..config import load
from .review_classes import ClassRow, class_rows
from .review_lessons import NEVER, Lesson, learnings_texts_at, parse_lessons
from .review_pitfalls import newest_rules, package_of, ranked_classes

START = "<!-- do-not-repeat:start -->"
END = "<!-- do-not-repeat:end -->"
WINDOW = timedelta(weeks=4)
TOP = 5
SECTION = re.compile(r"^## (\d+)\. .*?(?=^## |\Z)", re.MULTILINE | re.DOTALL)
RULE = re.compile(r"^\*\*Rule:\*\*\s*(.+?)(?:\n\n|\n\*\*)", re.MULTILINE | re.DOTALL)
SNIPPET = r"^\*\*{}\*\* \(.*?`([^`]+)`\):\n```\w*\n(.*?)\n```"
DONT = re.compile(SNIPPET.format("Don't"), re.MULTILINE | re.DOTALL)
DO = re.compile(SNIPPET.format("Do"), re.MULTILINE | re.DOTALL)


@dataclass(frozen=True)
class ClassRule:
    rule: str  # the section's **Rule:**, first sentence
    dont: tuple[str, str]  # (file, code)
    do: tuple[str, str]


def pitfalls_file(root: Path) -> Path:
    return root / str(load(root).get("knowledge.pitfalls"))


def class_rules(root: Path) -> dict[int, ClassRule]:
    """Return each class section's rule and Don't/Do snippets; a section lacking one is left out."""
    path = pitfalls_file(root)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    found = {}
    for section in SECTION.finditer(text):
        rule, dont, do = RULE.search(section[0]), DONT.search(section[0]), DO.search(section[0])
        if rule and dont and do:
            first = re.split(r"(?<=\.)\s", " ".join(rule[1].split()), maxsplit=1)[0]
            found[int(section[1])] = ClassRule(first, (dont[1], dont[2]), (do[1], do[2]))
    return found


def guides(root: Path) -> list[Path]:
    return sorted({p for pattern in load(root).get("knowledge.guides") for p in root.glob(pattern)})


def area_of(path: str, areas: list[str], root: Path) -> str | None:
    """Return the deepest guide folder holding the file's package, else None."""
    package = package_of(path, root)
    held = [a for a in areas if package and (package == a or package.startswith(f"{a}/"))]
    return max(held, key=len, default=None)


def placed(root: Path, areas: list[str], lessons: list[Lesson]) -> dict[str, list[Lesson]]:
    """Return guide folder -> its lessons in the four weeks to that area's own newest lesson.

    Per area, so a lesson moves only its own area's block.
    """
    found: dict[str, list[Lesson]] = defaultdict(list)
    for lesson in lessons:
        if lesson.when:
            for area in {area_of(f, areas, root) for f in lesson.files} - {None}:
                found[str(area)].append(lesson)
    for area, here in found.items():
        newest = max(x.when or NEVER for x in here)
        found[area] = [x for x in here if (x.when or NEVER) >= newest - WINDOW]
    return found


def class_lines(row: ClassRow, rule: ClassRule) -> list[str]:
    (dont_file, dont), (do_file, do) = rule.dont, rule.do
    return [
        f"- **Class {row.number}: {row.text}.** {rule.rule} Guard: {row.guard_cell}",
        # one fenced block per snippet: ruff formats Python blocks in Markdown, and two top-level
        # snippets in one block would need its blank lines between them
        "  ```python",
        f"  # Don't (as it would read in {dont_file})",
        *(f"  {line}".rstrip() for line in dont.splitlines()),
        "  ```",
        "  ```python",
        f"  # Do ({do_file})",
        *(f"  {line}".rstrip() for line in do.splitlines()),
        "  ```",
    ]


def block(lessons: list[Lesson], rows: list[ClassRow], rules: dict[int, ClassRule]) -> str:
    """Return the block for one area: its top classes with snippets, then its newest lessons."""
    by_class: dict[str, list[Lesson]] = defaultdict(list)
    for lesson in lessons:
        by_class[lesson.class_].append(lesson)
    row_of = {r.text: r for r in rows if r.number in rules}
    top = [row_of[n] for n in ranked_classes(by_class, rows) if n in row_of][:TOP]
    number = {r.text: r.number for r in rows}
    newest = newest_rules(lessons, number)[:TOP]
    lines = [
        START,
        "## Do not repeat (generated: `dl records rules --write`)",
        "",
        "The review classes most logged against this area in the four weeks to its newest lesson,",
        "then its newest lessons. Write against them; `dl records pitfalls <files>` gives a wider",
        "look. Do not edit by hand: the guard test regenerates and",
        "compares.",
        "",
    ]
    for row in top:
        lines += class_lines(row, rules[row.number])
    if not top:
        lines.append("- No lesson logged against this area in the window.")
    lines += ["", "Newest lessons here:", *(newest or ["- None in the window."]), END]
    return "\n".join(lines)


def with_block(text: str, new: str) -> str:
    """Replace the block between the markers, else insert it before the guide's first section."""
    if START in text and END in text:
        head, _, rest = text.partition(START)
        return head + new + rest.partition(END)[2]
    at = text.find("\n## ")
    if at < 0:
        return f"{text.rstrip()}\n\n{new}\n"
    return f"{text[:at].rstrip()}\n\n{new}\n\n{text[at + 1 :]}"


def expected(root: Path) -> dict[Path, str]:
    """Return each guide's text as the generator would write it now."""
    paths = guides(root)
    if paths and not load(root).get("knowledge.packages"):
        raise ValueError(
            f"knowledge.guides names {len(paths)} guides but knowledge.packages is empty, "
            "so no lesson can be placed in an area; nothing written. "
            "Set knowledge.packages (<root>[:<depth>][=<mirror>])."
        )
    areas = [p.parent.relative_to(root).as_posix() for p in paths]
    rows = class_rows(root)
    rules = class_rules(root)
    by_area = placed(root, areas, parse_lessons(root, rows))
    return {
        p: with_block(p.read_text(encoding="utf-8"), block(by_area.get(a, []), rows, rules))
        for p, a in zip(paths, areas, strict=True)
    }


def changed(root: Path) -> dict[Path, str]:
    """Return the guides whose text differs from what `rules --write` writes now, new text each."""
    return {p: t for p, t in expected(root).items() if p.read_text(encoding="utf-8") != t}


def generate(root: Path, *, write: bool = True) -> int | None:
    """Run the repo's own generator (`rules.generator`), or with `write` False its `rules.check`.

    None when no generator is set. A generator with no check has no staleness check: said, exit 0.
    """
    config = load(root)
    key = "generator" if write else "check"
    if not config.get("rules.generator"):
        return None
    command = config.get(f"rules.{key}")
    if not command:
        print("rules: blocks are written by rules.generator; no built-in staleness check")
        return 0
    done = subprocess.run(command, shell=True, cwd=root, check=False)  # noqa: S602 - the repo's own
    if done.returncode:
        print(f"rules.{key} exited {done.returncode}", file=sys.stderr)
    return done.returncode


def git(root: Path, *args: str) -> str:
    return subprocess.run(  # noqa: S603, S607 - fixed git arguments
        ["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout


def touched(root: Path, base: str) -> set[str]:
    """Return the guides a tree based on the git ref `base` must keep current.

    The guides of the areas where it adds, removes or changes a lesson, the guides it changed
    itself (untracked ones too), and every guide when it changed the pitfalls file
    (`knowledge.pitfalls`). A lesson another PR added (two merges that did
    not conflict as text) never fails this one.
    """
    names = [p.relative_to(root).as_posix() for p in guides(root)]
    classes = pitfalls_file(root).relative_to(root).as_posix()  # it changes every block
    if git(root, "diff", "--name-only", base, "--", classes).strip():
        return set(names)
    rows = class_rows(root)
    before = set(parse_lessons(root, rows, learnings_texts_at(root, base)))
    moved = before ^ set(parse_lessons(root, rows))
    areas = [name.rpartition("/")[0] for name in names]
    landed = {area_of(f, areas, root) for x in moved for f in x.files}
    edited = git(root, "diff", "--name-only", base, "--", *names).split()
    edited += git(root, "ls-files", "--others", "--exclude-standard", "--", *names).split()
    return {n for n, a in zip(names, areas, strict=True) if a in landed or n in edited}


def stale(root: Path, base: str | None = None) -> list[str]:
    """Return the repo paths of the guides whose block is stale; with `base`, the touched only."""
    found = [p.relative_to(root).as_posix() for p in changed(root)]
    return found if base is None else sorted(set(found) & touched(root, base))


def write(root: Path, files: list[str] | None = None) -> list[str]:
    """Write every stale guide's block (LF, as .gitattributes keeps .md); return the guides.

    With `files` (a lesson's), only the guides of the areas those files fall in.
    """
    found = changed(root)
    if files is not None:
        areas = [p.parent.relative_to(root).as_posix() for p in guides(root)]
        mine = {area_of(f, areas, root) for f in files}
        found = {p: t for p, t in found.items() if p.parent.relative_to(root).as_posix() in mine}
    for path, text in found.items():
        path.write_text(text, encoding="utf-8", newline=chr(10))
    return [p.relative_to(root).as_posix() for p in found]
