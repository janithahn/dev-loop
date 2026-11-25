"""Tests for review_rules.py, guide_resolve.py and review_evaluate.py.

Plants a class table, sections with snippets, guides and lessons in a temporary project whose
dev-loop.yaml names the guides and the pitfalls file.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from dev_loop.records.guide_resolve import main as resolve
from dev_loop.records.review_classes import class_rows
from dev_loop.records.review_evaluate import evaluate
from dev_loop.records.review_lessons import parse_lessons
from dev_loop.records.review_rules import (
    END,
    START,
    area_of,
    block,
    class_rules,
    placed,
    stale,
    touched,
    write,
)
from test_review_pitfalls import root  # noqa: F401 - the project fixture

PITFALLS = "REVIEW-PITFALLS.md"
AREA = "src/app/analysis"
FRONT = "web"
CASE = "web/src/features/case"
TABLE = (
    "| # | Class | Mined | Lessons since | Automated guard |\n|---|---|---|---|---|\n"
    "| 3 | Same thing twice | 1 (1) | 0 | `tests/guard_a.py` |\n"
    "| 9 | A failure swallowed | 1 (1) | 0 | review only: no shape |\n"
)


def section(number: int, title: str) -> str:
    return (
        f"\n## {number}. {title}\n**Rule:** First sentence {number}. Second sentence\n"
        "runs on.\n\n**Guard:** g.\n\n**Don't** (as it would read in `a.py`):\n```python\n"
        f"bad_{number}()\nbad()\nbad()\n```\n**Do** (`b.py`):\n```python\ngood_{number}()\n"
        "good()\ngood()\n```\n"
    )


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def lesson(when: str, class_: str, files: str, avoid: str) -> str:
    detail = f"Summary: s\nClass: {class_}\nGuard: review: r\nFiles: {files}\nRound: 1\n"
    action = f"Avoid: {avoid}. Self-check: look.\n"
    return f"## [L1] x\n\n**When:** {when} UTC\n\n### Detail\n{detail}\n### Action\n{action}"


def plant(root: Path) -> None:
    write_text(root / PITFALLS, TABLE + section(3, "Same thing twice") + section(9, "A failure"))
    for guide in (AREA, FRONT, CASE):
        write_text(root / guide / "CLAUDE.md", f"# {guide}\n\nIntro.\n\n## Key files\n- x\n")


def test_class_rules(root: Path) -> None:
    """The rule's first sentence and both snippets come from each class section."""
    plant(root)
    rules = class_rules(root)
    assert rules[3].rule == "First sentence 3.", rules[3]
    assert rules[3].dont == ("a.py", "bad_3()\nbad()\nbad()"), rules[3]
    assert rules[9].do == ("b.py", "good_9()\ngood()\ngood()"), rules[9]
    write_text(root / PITFALLS, TABLE + "\n## 3. Same thing twice\n**Rule:** r.\n")
    assert class_rules(root) == {}, "a section with no snippets is left out"


def test_area_of(root: Path) -> None:
    """The deepest guide holding the file's package wins; a test file maps to its system."""
    areas = [AREA, FRONT, CASE]
    assert area_of(f"{AREA}/checks/x.py", areas, root) == AREA
    assert area_of("tests/analysis/test_x.py", areas, root) == AREA
    assert area_of(f"{CASE}/CasePage.tsx", areas, root) == CASE
    assert area_of(f"{FRONT}/src/features/queue/Q.tsx", areas, root) == FRONT
    assert area_of("src/app/shared/x.py", areas, root) is None
    assert area_of("README.md", areas, root) is None


def test_block(root: Path) -> None:
    """Top classes by count, rowed only, with snippets; newest lessons first; window drops old."""
    plant(root)
    changes = root / ".dev-loop/changes"
    plants = {
        "a": ("2026-10-14 08:00", "9", f"{AREA}/x.py", "newest rule"),
        "b": ("2026-10-13 08:00", "9", f"{AREA}/y.py", "second rule"),
        "c": ("2026-10-12 08:00", "3", f"{AREA}/z.py", "third rule"),
        "d": ("2026-10-11 08:00", "new: Shiny", f"{AREA}/z.py", "unrowed rule"),
        "e": ("2026-09-01 08:00", "3", f"{AREA}/z.py", "too old"),  # outside four weeks
        "f": ("2026-10-10 08:00", "3", f"{CASE}/A.tsx", "case rule"),
        "g": ("2026-08-01 08:00", "9", f"{FRONT}/src/features/queue/Q.tsx", "front rule"),
    }
    for change, args in plants.items():
        write_text(changes / change / "learnings.md", lesson(*args))
    rows = class_rows(root)
    by_area = placed(root, [AREA, FRONT, CASE], parse_lessons(root, rows))
    assert {x.change for x in by_area[AREA]} == {"a", "b", "c", "d"}, "e is too old"
    assert [x.change for x in by_area[CASE]] == ["f"]
    assert [x.change for x in by_area[FRONT]] == ["g"], "the window is the area's own"
    text = block(by_area[AREA], rows, class_rules(root))
    lines = text.splitlines()
    assert lines[0] == START and lines[-1] == END
    heads = [line for line in lines if line.startswith("- **Class")]
    assert [h[:12] for h in heads] == ["- **Class 9:", "- **Class 3:"], heads  # Shiny unrowed
    assert "  bad_9()" in lines and "  good_3()" in lines
    newest = lines[lines.index("Newest lessons here:") + 1 :]
    assert newest[0].startswith("- class 9 · a:L1 · newest rule."), newest
    assert newest[3].startswith("- class ? · d:L1 · unrowed rule."), newest
    assert not any("too old" in line for line in lines)
    empty = block([], rows, class_rules(root))
    assert "- No lesson logged against this area in the window." in empty
    assert "- None in the window." in empty


def test_write_and_stale(root: Path) -> None:
    """Planted mismatch: a hand edit or a new lesson makes the guide stale until rewritten."""
    plant(root)
    first = lesson("2026-10-14 08:00", "9", f"{AREA}/x.py", "r1")
    write_text(root / ".dev-loop/changes/a/learnings.md", first)
    assert stale(root) == [f"{AREA}/CLAUDE.md", f"{FRONT}/CLAUDE.md", f"{CASE}/CLAUDE.md"]
    write(root)
    assert stale(root) == [], "written blocks are current"
    guide = (root / AREA / "CLAUDE.md").read_text(encoding="utf-8")
    assert guide.index(START) > guide.index("Intro.") and guide.index(END) < guide.index("## Key")
    assert "\r" not in (root / AREA / "CLAUDE.md").read_bytes().decode("utf-8")
    write(root)
    assert (root / AREA / "CLAUDE.md").read_text(encoding="utf-8") == guide, "idempotent"
    (root / AREA / "CLAUDE.md").write_text(guide.replace("r1.", "edited."), encoding="utf-8")
    assert stale(root) == [f"{AREA}/CLAUDE.md"], "a hand edit is stale"
    write(root)
    later = lesson("2026-10-15 08:00", "3", f"{AREA}/q.py", "r2")
    write_text(root / ".dev-loop/changes/b/learnings.md", later)
    assert stale(root) == [f"{AREA}/CLAUDE.md"], "a new lesson makes its area stale"


CONFLICT = (
    "intro\n<!-- do-not-repeat:start -->\n<<<<<<< HEAD\nmine\n||||||| base\nold\n=======\n"
    "upstream\n>>>>>>> theirs\n<!-- do-not-repeat:end -->\nprose\n"
)


def test_guide_resolve(tmp_path: Path) -> None:
    """A conflict inside the block takes the upstream side; one in the prose stops, file intact."""
    guide = tmp_path / "CLAUDE.md"
    guide.write_text(CONFLICT.replace("mine", "ours"), encoding="utf-8")
    assert resolve([str(guide)]) == 0
    text = guide.read_text(encoding="utf-8")
    assert "ours" in text and "<<<<" not in text and "upstream" not in text, text
    prose = "<<<<<<< HEAD\na\n=======\nb\n>>>>>>> t\n" + CONFLICT
    guide.write_text(prose, encoding="utf-8")
    with pytest.raises(SystemExit):
        resolve([str(guide)])
    assert guide.read_text(encoding="utf-8") == prose
    across = "<!-- do-not-repeat:start -->\n<<<<<<< HEAD\nx\n<!-- do-not-repeat:end -->\n"
    guide.write_text(across, encoding="utf-8")
    with pytest.raises(SystemExit, match="block marker"):
        resolve([str(guide)])


def test_evaluate(root: Path) -> None:
    """Per week the round-1 repeat rate overall and per class; a guard with 3+ since is weak."""
    plant(root)
    changes = root / ".dev-loop/changes"
    plants = [
        ("2026-10-13 08:00", "9", 1),
        ("2026-10-13 09:00", "9", 1),
        ("2026-10-13 10:00", "3", 1),
        ("2026-10-14 08:00", "new: Shiny", 1),
        ("2026-10-14 09:00", "3", 2),
        ("2026-10-06 08:00", "3", 1),
        ("2026-10-06 09:00", "3", 1),
    ]
    for i, (when, class_, round_) in enumerate(plants):
        text = lesson(when, class_, f"{AREA}/x.py", "r").replace("Round: 1", f"Round: {round_}")
        write_text(changes / f"c{i}/learnings.md", text)
    out = evaluate(root)  # no git in the temp root: every lesson counts
    assert out[1] == "| 2026-W42 | 4 | 3 | 75% | 9: 2 (50%) · 3: 1 (25%) |", out
    assert out[2] == "| 2026-W41 | 2 | 2 | 100% | 3: 2 (100%) |", out
    guard = [line for line in out if line.startswith("| 3 |")]
    assert guard == ["| 3 | `tests/guard_a.py` | — | 3 | weak |"], out


def commit(root: Path, message: str) -> None:
    for args in (["add", "-A"], ["commit", "-qm", message]):
        cmd = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
        subprocess.run([*cmd, *args], cwd=root, capture_output=True, check=True)


def test_base(root: Path) -> None:
    """With a base, only the guides this tree changes are judged.

    Another PR's lesson merged without its regenerated block (two merges that did not conflict
    as text) leaves that guide stale on the target; a PR that added nothing there still passes.
    """
    plant(root)
    changes = root / ".dev-loop/changes"
    write_text(changes / "a/learnings.md", lesson("2026-10-10 08:00", "9", f"{AREA}/x.py", "r1"))
    write(root)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    commit(root, "base")
    write_text(
        changes / "drift/learnings.md", lesson("2026-10-11 08:00", "3", f"{CASE}/A.tsx", "r2")
    )
    commit(root, "another PR merged its lesson, not its block")
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert stale(root) == [f"{CASE}/CLAUDE.md"], "the target itself is stale in CASE"
    write_text(changes / "mine/learnings.md", lesson("2026-10-12 08:00", "9", f"{AREA}/y.py", "r3"))
    assert stale(root, base) == [f"{AREA}/CLAUDE.md"], "this tree's lesson needs its block"
    guide = root / AREA / "CLAUDE.md"
    text = guide.read_text(encoding="utf-8")
    guide.write_text(text.replace("r1.", "r1 edited."), encoding="utf-8")
    write(root)
    case = root / CASE / "CLAUDE.md"
    case_text = case.read_text(encoding="utf-8")
    subprocess.run(["git", "checkout", "-q", "--", f"{CASE}/CLAUDE.md"], cwd=root, check=True)
    assert stale(root, base) == [], "CASE is stale on the target, but not this tree's to fix"
    case.write_text(case_text.replace("r2.", "hand edit."), encoding="utf-8")
    assert stale(root, base) == [f"{CASE}/CLAUDE.md"], "a guide this tree edits is judged"
    subprocess.run(["git", "checkout", "-q", "--", f"{CASE}/CLAUDE.md"], cwd=root, check=True)
    doc = root / PITFALLS
    doc.write_text(doc.read_text(encoding="utf-8") + "Changed.\n", encoding="utf-8")
    assert stale(root, base) == [f"{CASE}/CLAUDE.md"], "a class change judges every guide"


def test_base_family(root: Path) -> None:
    """A removed lesson and an untracked guide count."""
    plant(root)
    changes = root / ".dev-loop/changes"
    write_text(changes / "a/learnings.md", lesson("2026-10-10 08:00", "9", f"{AREA}/x.py", "r1"))
    write(root)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    commit(root, "base")
    base = "HEAD"
    (changes / "a/learnings.md").unlink()
    assert stale(root, base) == [f"{AREA}/CLAUDE.md"], "a removed lesson judges its area"
    write(root)
    assert stale(root, base) == []
    queue = f"{FRONT}/src/features/queue/CLAUDE.md"
    write_text(root / queue, "# queue\n")  # a new guide, not yet staged
    assert queue in touched(root, base)
    assert stale(root, base) == [queue], "an untracked guide is judged"


def no_packages(root: Path) -> Path:
    """The plugin's guides set, `knowledge.packages` empty: no lesson can be placed."""
    plant(root)
    (root / "dev-loop.yaml").write_text(
        'knowledge:\n  packages: []\n  guides: ["src/app/*/CLAUDE.md", "web/CLAUDE.md"]\n',
        encoding="utf-8",
    )
    write_text(root / ".dev-loop/changes/a/learnings.md",
               lesson("2026-10-14 08:00", "9", f"{AREA}/x.py", "r1"))
    return root / AREA / "CLAUDE.md"


def test_rules_refuses_a_block_it_cannot_place(
    root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from dev_loop.records import review_records

    guide = no_packages(root)
    before = guide.read_bytes()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    for argv in (["rules", "--write"], ["rules"]):
        assert review_records.main(argv) == 1
        err = capsys.readouterr().err
        assert "knowledge.guides names 2 guides but knowledge.packages is empty" in err
        assert "nothing written. Set knowledge.packages (<root>[:<depth>][=<mirror>])." in err
    assert guide.read_bytes() == before


def test_lesson_is_kept_when_rules_cannot_place(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dev_loop.records import lesson

    guide = no_packages(root)
    before = guide.read_bytes()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    (root / PITFALLS).write_text(TABLE, encoding="utf-8")
    (root / "dev-loop.yaml").write_text(
        (root / "dev-loop.yaml").read_text() + f"  pitfalls: {PITFALLS}\n", encoding="utf-8")
    write_text(root / "src/a.py", "")
    write_text(root / "tests/g.py", "")
    write_text(root / ".dev-loop/changes/c/proposal.md", "p")
    args = ["c", "high", "Summary: s", "Avoid: x. Self-check: y.", "Class: 3",
            "Guard: tests/g.py", "Files: src/a.py", "Round: 1"]
    assert lesson.main(args) == 0
    assert "Class: 3 Same thing twice" in (root / ".dev-loop/changes/c/learnings.md").read_text()
    assert guide.read_bytes() == before
