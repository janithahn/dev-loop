"""Review records and lessons: sample changes plus the frozen files, on a non-default layout.

The config names the changes folder and the pitfalls file; nothing is read from a fixed place.
Fixtures are synthetic.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


from dev_loop.records import record
from dev_loop.records.review_lessons import lesson_counts  # noqa: E402
from dev_loop.records.review_records import main, metric_rows, open_follow_ups  # noqa: E402
from test_neutral import ROOT, check  # noqa: E402

FU = "| PR | Thread | File | Finding | Why |\n|---|---|---|---|---|\n"
M = (
    "| PR | Change | Rounds | Per round (b/M/m) | Total (b/M/m) | Lessons |\n"
    "|---|---|---|---|---|---|\n"
)
DOC = (
    "| # | Class | Mined | Lessons since | Automated guard |\n|---|---|---|---|---|\n"
    "| 3 | Same thing twice | 1 (1) | 0 | `checks/guard_a.py` |\n"
    "| 9 | A failure swallowed, misread or hidden | 1 (1) | 0 | review only: no shape |\n"
)
PITFALLS = "docs/pitfalls.md"
CHANGES = "work/changes"  # not the default: every reader takes it from the config
FROZEN = "work/frozen"
T = "2026-10-07 10:00"


def configure(root: Path) -> None:
    config = {"paths": {"changes": CHANGES, "frozen": [FROZEN]}, "knowledge": {"pitfalls": PITFALLS}}
    write(root / "dev-loop.yaml", json.dumps(config))  # JSON is YAML


@pytest.fixture
def root(tree: object, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A temporary project whose config moves the changes folder and the pitfalls file."""
    configure(tree.root)  # type: ignore[attr-defined]
    return tree.root  # type: ignore[attr-defined]


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def follow_up(pr: int, thread: int, finding: str) -> dict[str, object]:
    fields = {"pr": pr, "thread": thread, "file": "`a.py`", "finding": finding, "why": "x"}
    return {"kind": "follow-up", **fields, "line": f"| {pr} | {thread} |", "legacy": True}


def metrics(pr: int) -> dict[str, object]:
    fields = {"pr": pr, "change": "c", "rounds": 1, "per_round": "0/0/1", "total": "0/0/1"}
    return {"kind": "metrics", **fields, "lessons": 0, "line": f"| {pr} |", "legacy": True}


def sidecar(*records: dict[str, object]) -> str:
    return "".join(json.dumps(r) + "\n" for r in records)


def lesson(when: str, *detail: str) -> str:
    body = "\n".join(detail)
    return f"## [L1] pattern - x\n\n**When:** {when} UTC\n\n### Detail\n{body}\n\n### Action\nA\n"



def test_followups_metrics_and_counts(root: Path) -> None:
    write(root / "docs/plans/FOLLOW-UPS.md", FU + "| 1 | 10 | `a.py` | old open | x |\n")
    one, two = root / CHANGES / "one", root / CHANGES / "two"
    rows = "| 2 | 20 | `b.py` | open b | x |\n| ~~3~~ | 30 | `c.py` | done | x |\n"
    rows += "| 5 | 50 | `e.py` | ~~only the finding struck~~ | x |\n"  # PR cell plain
    write(one / "follow-ups.md", FU + rows)
    write(two / "follow-ups.md", FU + "| 4 | 40 | `d.py` | open d | x |\n")
    frozen = FROZEN
    write(root / frozen / "records.jsonl", sidecar(follow_up(1, 10, "old open"), metrics(1)))
    sidecars = [follow_up(2, 20, "open b"), follow_up(3, 30, "done") | {"closed": True}]
    sidecars += [follow_up(5, 50, "~~only the finding struck~~") | {"closed": True}]
    write(one / "records.jsonl", sidecar(*sidecars, metrics(2)))
    write(two / "records.jsonl", sidecar(follow_up(4, 40, "open d")))
    write(root / PITFALLS, DOC)
    write(one / "learnings.md", "## [L1]\nSummary: 3. Same thing twice: cause (PR 2)\n")
    write(two / "learnings.md", "## [L1]\nSummary: 3. Same thing twice: cause\n")
    found = open_follow_ups(root)
    assert [line.split(":")[0] for line in found] == [
        f"{frozen}/FOLLOW-UPS.md",
        f"{CHANGES}/one/follow-ups.md",
        f"{CHANGES}/two/follow-ups.md",
    ], found  # struck class 3 left out; frozen first
    assert len(metric_rows(root)) == 2  # the frozen row and the change's row
    assert lesson_counts(root)[0][:3] == (2, 2, "Same thing twice")
    assert open_follow_ups(root / "nowhere") == []  # no records is not an error


def test_the_config_folder_is_the_only_one_read(root: Path) -> None:
    """A lesson under another folder is not read, whatever its name."""
    write(root / PITFALLS, DOC)
    write(root / "elsewhere/changes/x/learnings.md", lesson(T, "Summary: q", "Class: 3"))
    assert lesson_counts(root) == []


def test_command_reads_config(
    root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`dl records` takes the project from the environment and prints what the readers find."""
    write(root / PITFALLS, DOC)
    write(root / CHANGES / "one/learnings.md", lesson(T, "Summary: q", "Class: 3"))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    assert main(["lessons"]) == 0
    assert "Same thing twice" in capsys.readouterr().out
    assert main(["current"]) == 1  # the doc's guard path does not exist in this tree
    assert "guard cell" in capsys.readouterr().out


def test_this_task_leaves_its_files_neutral() -> None:
    src = ROOT / "plugins/dev-loop/src/dev_loop/records"
    names = ["review_records", "review_lessons", "review_classes", "review_distil", "review_guards"]
    paths = [src / f"{n}.py" for n in [*names, "review_evaluate"]]
    tests = ["test_review_records", "test_review_lessons", "test_distil", "test_impact_records",
             "test_review_guards"]
    paths += [Path(__file__).with_name(f"{n}.py") for n in tests]
    assert check(paths) == []
