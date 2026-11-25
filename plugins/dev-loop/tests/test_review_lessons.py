"""Lessons: parse, class resolution, current and trend, on a non-default layout.

The fixtures come from test_review_records: the config names the changes folder and the pitfalls
file, so nothing is read from a fixed place. Synthetic data only.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from test_review_records import CHANGES, DOC, PITFALLS, T, configure, lesson, root, write  # noqa: F401, I001
from dev_loop.records.review_lessons import class_rows, current, lesson_counts, parse_lessons, trend  # noqa: I001


def plant_lessons(changes: Path) -> None:
    new = ("Summary: cause", "Class: 9", "Guard: review: no shape")
    write(
        changes / "one/learnings.md",
        lesson("2026-10-05 08:00", *new, "Files: checks/guard_a.py", "Round: 1"),
    )  # the new shape, round 1
    write(
        changes / "two/learnings.md",
        lesson("2026-10-06 08:00", "Summary: A failure swallowed: legacy"),
    )  # no fields: the prefix fallback
    write(
        changes / "three/learnings.md",
        lesson(
            "2026-10-07 08:00",
            "Summary: y",
            "Class: new: Shiny",
            "Guard: checks/gone.py",
            "Files: a.py",
            "Round: 2",
        ),
    )  # a new class, a guard path that is gone, round 2
    write(changes / "four/learnings.md", lesson("2026-10-07 09:00", "Summary: Mystery: z"))


def counts_of(root: Path) -> dict[str, tuple[int, str]]:
    return {name: (c, g) for c, _, name, g in lesson_counts(root)}


def test_lessons(root: Path) -> None:
    """Fields and fallback, rules, trend, each on a planted doc."""
    write(root / PITFALLS, DOC)
    write(root / "checks/guard_a.py", "")
    changes = root / CHANGES
    plant_lessons(changes)
    counts = counts_of(root)
    assert counts["A failure swallowed, misread or hidden"] == (2, "review only: no shape")
    assert counts["new: Shiny"] == (1, "checks/gone.py")  # a new class: its Guard:
    faults = current(root)
    assert len(faults) == 1 and "gone.py" in faults[0], faults  # rule 3 only
    for other in ("five", "six"):
        write(
            changes / f"{other}/learnings.md",
            lesson(T, "Summary: q", "Class: new: Shiny"),
        )
    assert any("'new: Shiny' is in 3 changes" in f for f in current(root))  # rule 1
    write(root / PITFALLS, DOC.replace("`checks/guard_a.py`", "the test"))
    assert any("class 3 guard cell" in f for f in current(root))  # rule 2
    table = trend(root)
    assert table[1] == "| 2026-W41 | 6 | 1 | 2 | 3 | 100% |", table  # 1 round-1 of 2 repeats
    assert counts_of(root)["unclassified: Mystery"][0] == 1  # listed, never dropped


def test_fixes(root: Path) -> None:
    """Pre-PR check findings 1, 3 and 4 of lessons-loop-guards part a."""
    changes = root / CHANGES
    write(root / "checks/guard_a.py", "")
    # 3: no table, no rows: a fault, never a pass
    assert any("no class rows" in f for f in current(root))
    write(root / PITFALLS, DOC)
    assert current(root) == []
    # 1: the writer copies the first characters of the Detail into the header
    header = "## [L1] pattern - Summary: s\nClass: new: some na…\nGuard: back…\n\n"
    detail = (
        "### Detail\nSummary: s\nClass: 3\nGuard: review: r\n"
        "Files: checks/guard_a.py\nRound: 1\n"
    )
    stamp = "**When:** 2026-10-05 08:00 UTC\n\n"
    write(changes / "w/learnings.md", header + stamp + detail)
    made = parse_lessons(root, class_rows(root))[0]
    assert (made.class_, made.guard, made.files) == (
        "Same thing twice",
        "review: r",
        ("checks/guard_a.py",),
    ), made
    (changes / "w/learnings.md").unlink()
    # 4: a class cited by 3 changes with no row (class 12's row removed) is a fault
    for name in ("x1", "x2", "x3"):
        write(changes / f"{name}/learnings.md", lesson(T, "Summary: q", "Class: 12"))
    assert any("unclassified: 12" in f and "3 changes" in f for f in current(root))
    for name in ("x1", "x2", "x3"):
        (changes / f"{name}/learnings.md").unlink()


def test_fixes_more(root: Path) -> None:
    """Pre-PR check findings 5, 10 and 7 of lessons-loop-guards part a."""
    changes = root / CHANGES
    write(root / "checks/guard_a.py", "")
    write(root / PITFALLS, DOC)
    # 5: a stale Guard outside backend/ and .claude/ is a fault
    stale = lesson(T, "Summary: q", "Class: 3", "Guard: web/gone.test.ts")
    write(changes / "g/learnings.md", stale)
    assert any("web/gone.test.ts" in f for f in current(root))
    (changes / "g/learnings.md").unlink()
    # 10: legacy summaries "class 9: x" and "class 9, y" resolve to class 9
    write(changes / "l1/learnings.md", lesson(T, "Summary: class 9: x"))
    write(changes / "l2/learnings.md", lesson(T, "Summary: class 9, y"))
    assert counts_of(root)["A failure swallowed, misread or hidden"][0] == 2
    # 7: a week of round-1 new-class lessons has a rate of 0%, not a dash
    other = root / "other"
    configure(other)
    write(other / PITFALLS, DOC)
    write(other / CHANGES / "n/learnings.md", lesson(T, "Summary: q", "Class: new: Z", "Round: 1"))
    assert trend(other)[1].endswith("| 0% |"), trend(other)


def test_round_one(root: Path) -> None:
    """A class's stored text matches its row; a substring of a row text is not a row."""
    changes = root / CHANGES
    write(root / "checks/guard_a.py", "")
    row12 = "| 12 | {} | 1 (1) | 0 | review only: r |\n"
    write(root / PITFALLS, DOC + row12.format("Old cause"))
    for name in ("o1", "o2", "o3"):
        write(
            changes / f"{name}/learnings.md",
            lesson(T, "Summary: q", "Class: 12 Old cause"),
        )
    assert counts_of(root)["Old cause"][0] == 3  # stored text matches its row
    write(root / PITFALLS, DOC + row12.format("New  cause"))  # class 12's row replaced
    counts = counts_of(root)
    assert counts["unclassified: Old cause"][0] == 3 and "New  cause" not in counts, counts
    assert any("unclassified: Old cause" in f for f in current(root))  # no row: rule 1
    for name in ("o1", "o2", "o3"):
        (changes / f"{name}/learnings.md").unlink()
    write(root / PITFALLS, DOC)
    for name in ("t1", "t2", "t3"):  # a substring of a row text is not a row
        write(changes / f"{name}/learnings.md", lesson(T, "Summary: q", "Class: new: failure"))
    assert any("'new: failure' is in 3 changes" in f for f in current(root))
    write(changes / "t3/learnings.md", lesson(T, "Summary: q", "Class: new: Same  THING twice"))
    for name in ("t1", "t2"):
        same = lesson(T, "Summary: q", "Class: new: same thing twice")
        write(changes / f"{name}/learnings.md", same)
    assert current(root) == []  # a whole row text, any case or spacing, is rowed


def test_sharpened(root: Path) -> None:
    """A sharpened row keeps its lessons; a replaced not."""
    changes = root / CHANGES
    write(root / "checks/guard_a.py", "")
    row = "| 4 | {} | 1 (1) | 0 | review only: r |\n"
    write(root / PITFALLS, DOC + row.format("A test that passes"))
    for name in ("s1", "s2"):
        write(
            changes / f"{name}/learnings.md",
            lesson(T, "Summary: q", "Class: 4 A test that passes"),
        )
    sharper = "A test that would still pass with the bug in place"
    write(root / PITFALLS, DOC + row.format(sharper))  # reworded, no Formerly: a replaced row
    assert counts_of(root)["unclassified: A test that passes"][0] == 2
    section = f"\n## 4. {sharper}\n**Formerly:** Older title; a  TEST that passes\n"
    write(root / PITFALLS, DOC + row.format(sharper) + section)
    assert counts_of(root)[sharper][0] == 2, counts_of(root)  # sharpened: counted under class 4
    write(changes / "s3/learnings.md", lesson(T, "Summary: q", "Class: new: older TITLE"))
    write(changes / "s4/learnings.md", lesson(T, "Summary: q", "Class: new: Older title"))
    write(changes / "s5/learnings.md", lesson(T, "Summary: q", "Class: new: Older title"))
    assert current(root) == []  # a former title or a folded new: name is rowed too
    write(changes / "s6/learnings.md", lesson(T, "Summary: A test that passes: x"))
    # round 3: a folded new: name and a legacy prefix of a former title count here
    assert counts_of(root)[sharper][0] == 6, counts_of(root)


def git(root: Path, *args: str) -> str:
    cmd = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    done = subprocess.run(
        [*cmd, *args], cwd=root, capture_output=True, text=True, check=True, timeout=120,
        stdin=subprocess.DEVNULL,
    )
    return done.stdout.strip()


def plant_class(root: Path, class_: str, *names: str) -> None:
    for name in names:
        text = lesson(T, "Summary: q", f"Class: {class_}")
        write(root / f"{CHANGES}/{name}/learnings.md", text)


@pytest.mark.skipif(not shutil.which("git"), reason="git is needed")
def test_base(root: Path) -> None:
    """With a base, rule 1 fails only for a class this tree adds a lesson to."""
    git(root, "init", "-q")
    write(root / PITFALLS, DOC)
    plant_class(root, "new: X", "a", "b")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "base")
    base = git(root, "rev-parse", "HEAD")
    plant_class(root, "new: X", "c")  # this tree adds the third change
    assert any("'new: X' is in 3 changes" in f for f in current(root, base))
    git(root, "add", "-A")
    git(root, "commit", "-qm", "third")  # the base holds it in three; this tree adds elsewhere
    plant_class(root, "new: Y", "d")
    assert not any("new: X" in f for f in current(root, "HEAD")), current(root, "HEAD")
    assert any("new: X" in f for f in current(root))  # without a base: shared state, still a fault
    assert not any("new: X" in f for f in current(root, rule_one=False))  # the container mode

