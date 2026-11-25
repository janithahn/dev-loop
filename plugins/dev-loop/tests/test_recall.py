"""Tests for `recall` (`dl records recall`): the research listing, then three past lessons.

Plants lesson records in throwaway sidecars, each with its learnings.md line: a path match beats
more shared words, an exact file beats a wide PR, rarer words beat common ones, a history lesson
is placed through its change's merged PR, the change's own lessons and records off the grammar are
never recalled, and every degraded case says so.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dev_loop.records import review_distil, review_pitfalls
from dev_loop.records.record import MIGRATED, render
from dev_loop.records.review_pitfalls import recall
from test_review_pitfalls import git, root, write  # noqa: F401 - root: the project fixture

CHANGES = ".dev-loop/changes"


def lesson(
    lesson_id: str, summary: str, files: str | None, when: str = "2026-10-05 08:00"
) -> dict[str, Any]:
    """A lesson on the grammar; with no files, a migrated (legacy) one, as history lessons are."""
    found: dict[str, Any] = {"kind": "lesson", "id": lesson_id, "when": when, "priority": "medium"}
    found |= {"summary": summary, "class": "c", "guard": "g", "files": files, "round": 1}
    found |= {"avoid": f"avoid {lesson_id}"} | ({"legacy": True} if files is None else {})
    return found | {"line": render(found).lstrip().split("\n")[0]}


def plant_lessons(root: Path, change: str, *lessons: dict[str, Any]) -> None:
    """Write the change's sidecar and the learnings.md lines its records point at."""
    folder = root / CHANGES / change
    write(folder / "records.jsonl", "".join(json.dumps(x) + "\n" for x in lessons))
    write(folder / "learnings.md", "".join(render(x) for x in lessons))
    if any(x.get("legacy") for x in lessons):
        migrated = root / CHANGES / MIGRATED
        old = migrated.read_text(encoding="utf-8") if migrated.exists() else ""
        write(migrated, f"{old}{change}\n")


def plant(root: Path) -> None:
    """`me` plans the lesson ranker; past changes hold lessons alike in path or words."""
    proposal = "Rank lessons by keyword overlap, common and widely, in `scripts/rank.py`.\n"
    write(root / CHANGES / "me/proposal.md", proposal)
    plant_lessons(root, "me", lesson("L1", "rank lessons keyword overlap", "scripts/x.py"))
    plant_lessons(root, "path", lesson("L1", "nothing alike", "scripts/rank.py"))
    words = "rank lessons keyword overlap common widely"
    plant_lessons(root, "words", lesson("L1", words, "docs/a.md"))
    newer = lesson("L2", "overlap", "docs/b.md")
    plant_lessons(root, "rare", newer, lesson("L1", "keyword", "docs/c.md", "2026"))
    for name in ("other", "f1", "f2"):  # two words each, both common across the lessons
        plant_lessons(root, name, lesson("L1", "common widely", "docs/d.md", "2025"))
    write(root / "docs/research/a-study.md", "x")


def test_recall(root: Path) -> None:
    """Research first; path, then idf words, then newest; never the change's own lessons."""
    plant(root)
    forged = lesson("L1", "rank lessons keyword overlap", "scripts/rank.py")  # no Markdown line
    write(root / CHANGES / "forged/records.jsonl", json.dumps(forged) + "\n")
    write(root / CHANGES / "broken/records.jsonl", "not json\n")  # skipped, not a stop
    out = recall(root, "me", [])
    assert out[0] == "research (ls docs/research/): a-study.md", out
    assert out[1] == "note: no git; only lessons with files placed", out  # no repo
    assert out[2] == "2 bad sidecar lines skipped (dl records unparsed)", out
    ids = [line.split(" · ")[0] for line in out[3:]]
    # path shares the proposal's file; words shares the most words. v0.4.19: rare:L2 shares one
    # word, under the 2 x ln(N) floor an off-place lesson needs, so only two are recalled.
    # forged (off the grammar) is never recalled.
    assert ids == ["- path:L1", "- words:L1"], out
    assert out[3] == "- path:L1 · nothing alike · avoid: avoid L1", out
    assert all("me:" not in line for line in out), "the change's own lesson is never recalled"
    given = recall(root, "me", ["scripts/zz/a.py"])  # the files to touch replace the proposal's
    assert given[3].startswith("- words:L1"), given
    assert recall(root, "nope", [])[1:] == ["recall unavailable: no change nope"]
    assert recall(root, "", [])[1:] == ["recall unavailable: no change "]


def test_recall_history(root: Path) -> None:
    """A lesson without files follows its change's merged PR; no lessons and no git say so."""
    write(root / f"{CHANGES}/me/proposal.md", "nothing shared\n")
    assert recall(root, "me", [])[1:] == ["recall: no past lessons"]
    assert recall(root, "me", [])[0] == "research (ls docs/research/): none"
    plant_lessons(root, "h", lesson("L1", "zzz", None))
    write(root / f"{CHANGES}/h/status.md", "| 12 | part a | 1 | 0/0/0 | 0/0/0 | 1 |\n")
    plant_lessons(root, "n", lesson("L1", "zzz", None, "2026-10-09 08:00"))
    write(root / "scripts/hook.py", "")
    no_git = recall(root, "me", ["scripts/new.py"], ref="no-such-ref")
    # v0.4.19: n cannot be placed without git and shares no word: not near
    assert no_git[1].startswith("note: ") and no_git[2:] == [NONE_NEAR], no_git
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "Merged PR 12: history")
    out = recall(root, "me", ["scripts/new.py"])
    assert out[1].startswith("- h:L1"), out  # placed in scripts/ by PR 12, so above the newer n
    # A lesson on the very file beats one whose PR touched it and other places
    plant_lessons(root, "x", lesson("L1", "zzz", "scripts/new.py", "2026-01-01 08:00"))
    ids = [line.split(" · ")[0] for line in recall(root, "me", ["scripts/new.py"])[1:]]
    assert ids == ["- x:L1", "- h:L1"], ids  # n has no merged PR: not placed, not near


def test_recall_hardening(root: Path) -> None:
    """dev-loop-recall-hardening: a garbled lesson, a slashed name, nulls, non-UTF-8, one read."""
    plant(root)
    # A migrated lesson whose avoid holds a second lesson's fields
    mixed = lesson("L1", "rank", "scripts/rank.py") | {"avoid": "pin it Summary: other Class: 3"}
    plant_lessons(root, "mixed", mixed)
    # A migrated lesson with no summary and no avoid prints dashes
    empty = lesson("L1", "x", "scripts/rank.py", "2026-10-06 08:00")
    empty |= {"summary": None, "avoid": None, "legacy": True}
    plant_lessons(root, "nulls", empty | {"line": render(empty).lstrip().split("\n")[0]})
    write(root / CHANGES / "latin/records.jsonl", "")
    (root / CHANGES / "latin/records.jsonl").write_bytes(b"caf\xe9\n")  # not UTF-8
    calls: list[str] = []
    real = review_pitfalls.past_lessons

    def counted(*args: Any) -> Any:
        calls.append("read")
        return real(*args)

    review_pitfalls.past_lessons = review_distil.past_lessons = counted  # type: ignore[assignment]
    try:
        out = recall(root, "me", [])
    finally:
        review_pitfalls.past_lessons = review_distil.past_lessons = real  # type: ignore[assignment]
    assert len(calls) == 1, calls  # the sidecars are read once
    # v0.4.19: read, cut before the second lesson's label, not skipped
    assert "- mixed:L1 · rank · avoid: pin it" in out, out
    assert not any("Summary:" in line or "lessons skipped" in line for line in out), out
    assert "1 bad sidecar lines skipped (dl records unparsed)" in out, out
    assert "- nulls:L1 · — · avoid: —" in out, out
    assert "- path:L1 · nothing alike · avoid: avoid L1" in out, out  # the others still recalled
    # A trailing slash names the same change; its own lessons stay out
    assert recall(root, "me/", []) == out, recall(root, "me/", [])


def test_a_lesson_that_names_a_heading_is_not_skipped(root: Path) -> None:
    plant(root)
    avoid = "count the '## [Ln]' headings. Self-check: every ## Build heading has a body"
    plant_lessons(root, "quoted", lesson("L1", "rank", "scripts/rank.py") | {"avoid": avoid})
    out = recall(root, "me", [])
    assert any(line.startswith("- quoted:L1") for line in out), out
    assert not any("lessons skipped" in line for line in out), out
    ran = lesson("L1", "rank", "scripts/rank.py") | {"avoid": "pin it ## [L9] 2026-10-01 other"}
    plant_lessons(root, "ran", ran)
    assert "- ran:L1 · rank · avoid: pin it" in recall(root, "me", [])  # v0.4.19: cut, shown


NONE_NEAR = "recall: no past lesson near this change"


def ids_of(out: list[str]) -> list[str]:
    return [line.split(" · ")[0] for line in out if line.startswith("- ")]


def test_an_off_place_lesson_needs_more_than_a_common_word(root: Path) -> None:
    """v0.4.19: a relevance floor; a far lesson sharing only a common word is not recalled."""
    write(root / CHANGES / "me/proposal.md", "Fix the button in `web/src/features/case/a.tsx`.\n")
    for name in ("dash", "panel", "chart"):  # `button` is in every lesson: it weighs nothing
        plant_lessons(root, name, lesson("L1", f"button {name}", f"src/app/{name}/a.py"))
    out = recall(root, "me", [])
    assert ids_of(out) == [] and out[-1] == NONE_NEAR, out
    plant_lessons(root, "case", lesson("L1", "zzzz", "web/src/features/case/b.tsx"))
    out = recall(root, "me", [])
    assert ids_of(out) == ["- case:L1"] and NONE_NEAR not in out, out  # the place, no words


def test_with_no_places_only_a_rare_word_is_near(root: Path) -> None:
    """With no paths and no backticked path, a word score of at least ln(N) is needed."""
    write(root / CHANGES / "me/proposal.md", "Tune the scorer for pricing.\n")
    plant_lessons(root, "rare", lesson("L1", "scorer tuning", "docs/r.md"))
    for name in ("s1", "s2", "s3"):
        plant_lessons(root, name, lesson("L1", f"pricing {name}", "docs/s.md"))
    assert ids_of(recall(root, "me", [])) == ["- rare:L1"]


def test_run_together_lessons_are_read_not_skipped(root: Path) -> None:
    """A legacy one-line lesson and two lessons run together are cut at the embedded label."""
    plant(root)
    one_line = "Pin the floor: Root cause: none. Avoid: rank with a floor. Self-check: far hidden"
    legacy = lesson("L1", one_line, "scripts/rank.py") | {"avoid": "rank with a floor"}
    plant_lessons(root, "legacy", legacy)
    two = "pin the floor. Self-check: far is hidden. Summary: the next lesson Class: 3"
    plant_lessons(root, "two", lesson("L1", "rank", "scripts/rank.py") | {"avoid": two})
    out = recall(root, "me", [])
    assert "- legacy:L1 · Pin the floor: Root cause: none · avoid: rank with a floor" in out, out
    assert "- two:L1 · rank · avoid: pin the floor. Self-check: far is hidden" in out, out
    assert not any("lessons skipped" in line for line in out), out


def test_an_embedded_avoid_fills_an_empty_avoid(root: Path) -> None:
    plant(root)
    bare = lesson("L1", "Pin it: Root cause: x. Avoid: rank first. Class: 3", "scripts/rank.py")
    bare |= {"avoid": None, "legacy": True}  # a migrated lesson: its avoid may be empty
    plant_lessons(root, "bare", bare | {"line": render(bare).lstrip().split("\n")[0]})
    empty = lesson("L1", "Summary: only labels", "scripts/rank.py")  # nothing before the label
    plant_lessons(root, "empty", empty)
    out = recall(root, "me", [])
    assert "- bare:L1 · Pin it: Root cause: x · avoid: rank first" in out, out
    assert "1 lessons skipped: their fields ran together" in out, out
