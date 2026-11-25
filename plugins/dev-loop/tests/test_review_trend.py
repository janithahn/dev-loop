"""The dated-fold rule of `trend` and `review_trend`'s changed-line count.

A folded `new:` lesson counts as a repeat only when written after the fold date; a PR's size
leaves out the generated files `size.generated` names.
"""

from __future__ import annotations

from pathlib import Path

from dev_loop.records.review_lessons import lesson_counts, trend
from dev_loop.records.review_trend import changed_lines
from test_review_pitfalls import git, root, write  # noqa: F401 - root: the project fixture

DOC = (
    "| # | Class | Mined | Lessons since | Automated guard |\n|---|---|---|---|---|\n"
    "| 3 | Same thing twice | 1 (1) | 0 | `tests/guard_a.py` |\n"
    "| 9 | A failure swallowed, misread or hidden | 1 (1) | 0 | review only: no shape |\n"
)
PITFALLS = "REVIEW-PITFALLS.md"
CHANGES = ".dev-loop/changes"
ROW = "| 4 | Newer title | 1 (1) | 0 | review only: r |\n"


def lesson(when: str, *detail: str) -> str:
    body = "\n".join(detail)
    return f"## [L1] pattern - x\n\n**When:** {when} UTC\n\n### Detail\n{body}\n\n### Action\nA\n"


def counts_of(root: Path) -> dict[str, tuple[int, str]]:
    return {name: (c, g) for c, _, name, g in lesson_counts(root)}


def test_trend_fold(root: Path) -> None:
    """A folded `new:` lesson is a repeat only when written after the fold date."""
    section = "\n## 4. Newer title\n**Formerly:** Spare; Older title (folded 2026-10-10)\n"
    write(root / PITFALLS, DOC + ROW + section)
    before = lesson("2026-10-06 08:00", "Summary: q", "Class: new: Older title")
    after = lesson("2026-10-15 08:00", "Summary: q", "Class: new: older TITLE")
    write(root / CHANGES / "e/learnings.md", before)
    write(root / CHANGES / "f/learnings.md", after)
    assert counts_of(root)["Newer title"][0] == 2  # both resolve to the row, as before
    table = trend(root)
    assert table[1] == "| 2026-W42 | 1 | — | 1 | 0 | — |", table  # after the fold: a repeat
    assert table[2] == "| 2026-W41 | 1 | — | 0 | 1 | — |", table  # before it: still new
    write(root / PITFALLS, DOC + ROW + section.replace(" (folded 2026-10-10)", ""))
    assert trend(root)[2] == "| 2026-W41 | 1 | — | 1 | 0 | — |"  # an undated fold: a repeat


def test_trend_week(root: Path) -> None:
    """Minor 8: inside one week the fold date, not the week, splits new from repeat."""
    section = "\n## 4. Newer title\n**Formerly:** Older title (folded 2026-10-07)\n"
    write(root / PITFALLS, DOC + ROW + section)
    old = "Class: new: Older title"
    write(root / CHANGES / "e/learnings.md", lesson("2026-10-07 08:00", "Summary: q", old))
    write(root / CHANGES / "f/learnings.md", lesson("2026-10-08 08:00", "Summary: q", old))
    table = trend(root)
    assert table[1] == "| 2026-W41 | 2 | — | 1 | 1 | — |", table  # 10-07 new, 10-08 a repeat


def test_changed_lines(root: Path) -> None:
    """A merged PR counts added plus deleted lines, not generated files; unmerged: None."""
    config = root / "dev-loop.yaml"
    config.write_text(config.read_text(encoding="utf-8") + 'size:\n  generated: ["gen/*"]\n')
    write(root / "a.py", "one\n")
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "start")
    git(root, "checkout", "-qb", "work")
    write(root / "a.py", "one\ntwo\nthree\n")
    write(root / "gen/x.txt", "g\n" * 50)
    write(root / "package-lock.json", "{}\n" * 20)
    git(root, "add", "-A")
    git(root, "commit", "-qm", "change")
    git(root, "checkout", "-q", "-")
    git(root, "merge", "--no-ff", "-qm", "Merged PR 7: work", "work")
    assert changed_lines(root, 7, "HEAD") == 2
    assert changed_lines(root, 8, "HEAD") is None
