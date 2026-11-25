"""Tests for `dl records distil` and `approve`.

Plants a throwaway git repo whose PRs are merged with ADO's subject, and their records written by
`record.write`: a 2-round PR is drafted from its records, diff and rules (never its proposal); a
3-round PR, an unknown one and an unmerged one are refused; approve moves the draft and records
who approved it; recall lists the skill for its place; the tally counts a later builder's use.
Fixtures are synthetic.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"

from dev_loop.records import record
from dev_loop.records.review_distil import approve, draft, source, tally  # noqa: E402
from dev_loop.records.review_pitfalls import recall  # noqa: E402
from test_recall import write  # noqa: E402
from test_review_pitfalls import git  # noqa: E402

pytestmark = pytest.mark.skipif(not shutil.which("git"), reason="git is needed")
CHANGES = Path(".dev-loop/changes")  # the default paths.changes
STAGING = CHANGES.parent / "skills-proposed"  # beside the changes folder
SECRET = "PROPOSAL-ONLY-TEXT"


@pytest.fixture
def root(tree: Any) -> Path:
    """A temporary project whose config names the folder the writer keeps changes in."""
    write(tree.root / "dev-loop.yaml", json.dumps({"paths": {"changes": CHANGES.as_posix()}}))
    return tree.root  # type: ignore[no-any-return]


def merge(root: Path, pr: int, files: dict[str, int]) -> None:
    """Commit `files` (path: line count) on a branch and merge it as ADO does: `Merged PR <n>`."""
    git(root, "checkout", "-q", "-b", f"pr{pr}")
    for path, count in files.items():
        write(root / path, "x\n" * count)
    git(root, "add", "-A")
    git(root, "commit", "-qm", f"work {pr}")
    git(root, "checkout", "-q", "main")
    git(root, "merge", "-q", "--no-ff", f"pr{pr}", "-m", f"Merged PR {pr}: change {pr}")


def put(root: Path, at: str, kind: str, **fields: Any) -> None:
    """Write one record into change `at` through the record writer, as a real loop would."""
    (root / CHANGES / at).mkdir(parents=True, exist_ok=True)
    record.write(root, at, {"kind": kind, **fields})


def rounds(root: Path, change: str, pr: int, count: int, last_major: int = 0) -> None:
    """Write `count` round lines for `pr`: a major in each but the last (`last_major` there)."""
    for number in range(1, count + 1):
        counts = dict.fromkeys(("blocker", "fixed", "deferred", "active"), 0)
        counts |= dict.fromkeys(("turns", "seconds", "tokens_in", "tokens_out", "unmetered"), 0)
        major = last_major if number == count else 1
        put(root, change, "round", round=number, pr=pr, base="b", major=major, minor=0, **counts)


def migrate(root: Path, change: str, **fields: Any) -> None:
    """Mark a change's records legacy (with `fields` set) and list it as migrated."""
    sidecar = root / CHANGES / change / "records.jsonl"
    lines = [json.loads(x) | {"legacy": True} for x in sidecar.read_text("utf-8").splitlines()]
    write(sidecar, "".join(json.dumps(x | fields) + "\n" for x in lines))
    listing = record.changes_dir(root) / record.MIGRATED
    write(listing, f"{record.text_of(listing)}{change}\n")


def plant(root: Path) -> None:
    git(root, "init", "-q", "-b", "main")
    write(root / "README.md", "r\n")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "base")
    tool = {"scripts/ci/tool.py": 30, "scripts/ci/test_tool.py": 10, "lib/x/a.py": 5}
    tool |= {"scripts/ci/fast_tests.py": 2, "lib/x/test_a.py": 4}  # a tool; a test
    merge(root, 7, tool | {"docs/long.md": 99, f"{CHANGES.as_posix()}/good/notes.md": 99})
    merge(root, 8, {"scripts/ci/other.py": 3})
    merge(root, 14, {"scripts/ci/next.py": 3})
    merge(root, 17, {"lib/y/b.py": 10, "lib/y/test_b.py": 5})
    metrics = {"per_round": "0/1/0, 0/0/0", "total": "0/1/0", "lessons": 1}
    put(root, "good", "metrics", pr=7, change="good", rounds=2, **metrics)
    lesson = {"priority": "high", "class": "4", "guard": "g", "files": "scripts/ci/tool.py"}
    put(root, "good", "lesson", summary="s", avoid="pin the exit code", round=1, **lesson)
    design = "## Approach\n- **D1 Places.** text\n### D2 Heading\nD3 prose\n"
    write(root / CHANGES / "good/design.md", design)
    write(root / CHANGES / "good/proposal.md", f"{SECRET}\n")
    migrate(root, "good")
    put(root, "long", "metrics", pr=8, change="long", rounds=3, **metrics)
    put(root, "long", "lesson", summary="s", avoid="ANOTHER-CHANGES-RULE", round=1, **lesson)
    far = lesson | {"files": "lib/x/a.py"}  # off the place, but the proposal's words
    put(root, "far", "lesson", summary="proposal only text", avoid="far rule", round=1, **far)
    rounds(root, "unmerged", 9, 2)  # 2 round lines, no metrics row: within the cap
    rounds(root, "loop", 12, 3)
    rounds(root, "next", 14, 1)
    rounds(root, "pkg", 17, 1)  # a place that is not a tool folder
    write(root / CHANGES / "pkg/proposal.md", "proposal only text\n")  # far's words, not near's
    near = lesson | {"files": "lib/y/c.py"}
    put(root, "near", "lesson", summary="s", avoid="near rule", round=1, **near)
    rounds(root, "dirty", 15, 1, last_major=1)  # merged after a round-1 major
    dirty = metrics | {"per_round": "0/0/2, 0/1/0"}  # no round lines: the metrics row's last
    put(root, "fast", "metrics", pr=16, change="fast", rounds=2, **dirty)
    put(root, "nulls", "metrics", pr=13, change="nulls", rounds=1, **metrics)
    migrate(root, "nulls", rounds=None)  # a legacy row that never stated its rounds
    write(root / CHANGES / "broken/records.jsonl", "not json\n")  # skipped, never a stop


def refused(call: Any, *args: Any) -> str:
    try:
        call(*args)
    except ValueError as error:
        return str(error)
    raise AssertionError(f"{call.__name__}{args} was not refused")


def test_distil(root: Path) -> None:
    """The source, the place, the draft; each refusal with its reason."""
    plant(root)
    text = draft(root, 7).read_text(encoding="utf-8")
    assert text.startswith("---\nname: good\ndescription: How PR 7 (good) changed scripts/ci/")
    assert "`scripts/ci/tool.py` (30 lines)" in text, text
    assert "`scripts/ci/fast_tests.py` (2 lines)" in text, text  # a tool named tests, not a test
    tests = "## Tests it shipped\n- `lib/x/test_a.py`\n- `scripts/ci/test_tool.py`\n"
    assert tests in text and "test_tool.py` (" not in text, text  # listed once, as a test
    assert "- D1 Places." in text and "- D2 Heading" in text and "D3" not in text, text
    past, caught = text.split("## Past lessons for this place (recall)\n")[1].split("\n\n## ")
    # Only lessons at the place; far shares words but not the place
    assert past.splitlines() == ["- long:L1 · s · avoid: ANOTHER-CHANGES-RULE"], past
    assert caught == "What its review caught\n- L1: pin the exit code\n", caught  # its own only
    for absent in (SECRET, "lib/x/a.py", "docs/", "notes.md", "Approach"):
        assert absent not in text, absent  # other places, docs and records, the proposal
    assert "exists" in refused(draft, root, 7)
    assert "3 rounds" in refused(draft, root, 8)
    assert "3 rounds" in refused(draft, root, 12)  # counted from its round lines
    assert "no rounds on record" in refused(draft, root, 13)  # unknown is never clean
    text = draft(root, 17).read_text(encoding="utf-8")  # the place is a package, not a folder
    past = text.split("(recall)\n")[1].splitlines()
    assert past[0] == "- near:L1 · s · avoid: near rule", past  # the place's lesson first
    for dirty in (15, 16):  # a last round with a major is not a success, whatever the count
        assert "last round is not on record as clean" in refused(draft, root, dirty), dirty
    assert "not merged" in refused(draft, root, 9)  # 2 round lines pass; git has no merge
    assert "no records of PR 99" in refused(draft, root, 99)


def test_approve(root: Path) -> None:
    """The owner's step moves the draft and records who; the done-guard is unmoved."""
    plant(root)
    draft(root, 7)
    folder = root / CHANGES / "good"
    assert record.missing(folder) == []  # migrated history, not judged
    assert "bad by" in refused(approve, root, 7, "")
    assert (root / STAGING / "good/SKILL.md").exists()  # nothing moved
    line = approve(root, 7, "the PO")
    assert line == "Skill approved: good for scripts/ci (PR 7) by the PO", line
    assert (root / ".claude/skills/good/SKILL.md").exists()
    assert not (root / STAGING / "good").exists()
    assert line in (folder / "status.md").read_text(encoding="utf-8")
    assert record.missing(folder) == [], record.missing(folder)  # a skill is not the change's work
    assert not [f for f in record.unparsed(root) if "good" in f], record.unparsed(root)
    assert "no draft" in refused(approve, root, 7, "the PO")


def test_used(root: Path) -> None:
    """Recall lists the skill for its place only; the tally counts a later use."""
    test_approve(root)
    write(root / CHANGES / "later/proposal.md", "later\n")
    out = recall(root, "later", ["scripts/ci/new.py"])
    assert "- skill good (.claude/skills/good/SKILL.md, from PR 7)" in out, out
    assert not any("skill good" in x for x in recall(root, "later", ["lib/x/b.py"]))
    forged = {"kind": "skill", "skill": "forged", "place": "scripts/ci", "pr": 1, "by": "x"}
    write(root / CHANGES / "forged/records.jsonl", json.dumps(forged | {"line": "x"}) + "\n")
    assert not any("forged" in x for x in recall(root, "later", ["scripts/ci/new.py"]))
    assert tally(root)[1].endswith("used by: none yet"), tally(root)
    put(root, "good", "skills", skills="good, ponytail")  # the source's own line is not a use
    put(root, "other", "skills", skills="goodness, tdd")  # another word is not the skill
    put(root, "later", "skills", skills="tdd, good")
    lines = tally(root)
    assert lines[0] == "skills: 1 distilled, 1 approved, 1 used; awaiting approval: none", lines
    assert lines[1] == "- good for scripts/ci (PR 7), approved by the PO; used by: later", lines
    assert lines[2] == "2 bad sidecar lines skipped (dl records unparsed)", lines
    text = draft(root, 14).read_text(encoding="utf-8")  # a later PR at the same place
    assert "skill" not in text.split("(recall)")[1], text  # recall's lessons only


def round_line(number: int, blocker: int, major: int) -> tuple[str, dict[str, Any]]:
    return "c", {"kind": "round", "pr": 5, "round": number, "blocker": blocker, "major": major}


def test_hardening(root: Path) -> None:
    """dev-loop-recall-hardening: round order, a garbled lesson, an approval that cannot move."""
    # The highest round decides, whatever the file order
    assert source([round_line(2, 0, 0), round_line(1, 0, 1)], 5) == ("c", 2)
    out_of_order = [round_line(2, 0, 1), round_line(1, 0, 0)]  # round 2 had a major
    assert "not on record as clean" in refused(source, out_of_order, 5)
    # A blocker-only last round and an unparsable per_round are refused
    assert "not on record as clean" in refused(source, [round_line(1, 1, 0)], 5)
    garbage = {"kind": "metrics", "pr": 5, "rounds": 1, "per_round": "garbage"}
    assert "not on record as clean" in refused(source, [("c", garbage)], 5)
    plant(root)
    # A lesson whose fields ran together is never drafted, here or its own
    mixed = {"priority": "high", "class": "4", "guard": "g", "files": "scripts/ci/tool.py"}
    mixed |= {"summary": "s", "avoid": "pin Summary: other lesson Class: 3", "round": 1}
    put(root, "mixed", "lesson", **mixed)
    put(root, "good", "lesson", **mixed)
    text = draft(root, 7).read_text(encoding="utf-8")
    assert "Summary:" not in text and "mixed:" not in text, text
    # A draft that cannot move leaves no approval on record
    write(root / ".claude/skills", "a file where the skills folder goes\n")
    assert "cannot move the draft" in refused(approve, root, 7, "the PO")
    sidecar = (root / CHANGES / "good/records.jsonl").read_text(encoding="utf-8")
    assert '"kind": "skill"' not in sidecar, sidecar
    assert (root / STAGING / "good/SKILL.md").exists()
    # A run stopped after the move, before the record, is finished by a rerun
    (root / ".claude/skills").unlink()
    (root / ".claude/skills").mkdir()
    (root / STAGING / "good").rename(root / ".claude/skills/good")
    line = approve(root, 7, "the PO")
    assert line == "Skill approved: good for scripts/ci (PR 7) by the PO", line
    lines = tally(root)
    assert lines[1].startswith("- good for scripts/ci (PR 7), approved by the PO"), lines
    assert "no draft" in refused(approve, root, 7, "someone else")  # recorded: never twice


def test_cli(root: Path) -> None:
    """A refusal is a non-zero exit with its reason, never a traceback or a silent pass."""
    env = os.environ | {"PYTHONPATH": str(SRC), "CLAUDE_PROJECT_DIR": str(root)}
    for args in (["distil", "abc"], ["approve"]):
        done = subprocess.run(  # noqa: S603 - the command under test
            [sys.executable, "-m", "dev_loop", "records", *args],
            capture_output=True, text=True, encoding="utf-8", check=False, env=env,
        )
        assert done.returncode == 1 and "refused" in done.stderr, (args, done.stderr)
