"""`dl records examples`: draft candidates from a project's own records, on a non-default layout.

The config names the changes folder; nothing is read from a fixed place. Fixtures are synthetic.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dev_loop.records import review_examples
from dev_loop.records.review_records import main
from test_neutral import ROOT, check

CHANGES = "work/changes"  # not the default: the reader takes it from the config
WHEN = "2026-10-07 10:00"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def lesson(summary: str, cls: str, lesson_id: str, avoid: str = "A") -> dict[str, object]:
    return {"kind": "lesson", "id": lesson_id, "when": WHEN, "summary": summary, "avoid": avoid,
            "class": cls, "guard": "open", "files": "a.py", "round": 1, "line": summary}


def follow_up(pr: int, finding: str, why: str = "can wait") -> dict[str, object]:
    return {"kind": "follow-up", "pr": pr, "thread": pr * 10, "file": "`b.py`",
            "finding": finding, "why": why, "line": f"| {pr} |"}


def sidecar(*records: dict[str, object]) -> str:
    return "".join(json.dumps(r) + "\n" for r in records)


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config = {"paths": {"changes": CHANGES, "archive": f"{CHANGES}/archive"}}
    write(tmp_path / "dev-loop.yaml", json.dumps(config))  # JSON is YAML
    one, two = tmp_path / CHANGES / "one", tmp_path / CHANGES / "two"
    write(one / "records.jsonl", sidecar(
        lesson("a swallowed error hid a failed step", "9 A failure swallowed", "L1"),
        follow_up(2, "rename d to config")))
    write(two / "records.jsonl", sidecar(
        lesson("a new branch shipped with no test", "3 Same thing twice", "L1"),
        follow_up(3, "a struck minor") | {"closed": True}))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    return tmp_path


def test_draft_lists_the_right_candidates(root: Path) -> None:
    out = review_examples.draft(root)
    assert "a swallowed error hid a failed step" in out  # a lesson = a fixed blocker/major
    assert "a new branch shipped with no test" in out  # the other change's lesson
    assert "rename d to config" in out  # an open deferred minor
    assert "a struck minor" not in out  # a closed follow-up is left out
    assert '"severity": "major"' in out and '"severity": "minor"' in out  # findings in shape
    assert '"rule": "a-failure-swallowed"' in out  # the class slugged into a rule
    assert "does not record findings closed as not-a-problem" in out  # the not-filed note


def test_draft_findings_validate(root: Path) -> None:
    from dev_loop.review.review_verdict import finding_errors

    out = review_examples.draft(root)
    objects = [json.loads(line.split(": ", 1)[1]) for line in out.splitlines()
               if line.lstrip().startswith("- change")]
    assert len(objects) == 3  # two lessons and one open follow-up
    for obj in objects:
        assert finding_errors("draft", obj) == [], obj


def test_limit_caps_each_source(root: Path) -> None:
    out = review_examples.draft(root, limit=1)  # the most recent lesson only
    assert "a new branch shipped with no test" in out
    assert "a swallowed error hid a failed step" not in out


def test_command_reads_config(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["examples"]) == 0
    assert "Candidate review examples" in capsys.readouterr().out
    assert main(["examples", "--limit", "1"]) == 0
    assert "a new branch shipped with no test" in capsys.readouterr().out


def test_no_records_is_not_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write(tmp_path / "dev-loop.yaml", json.dumps({"paths": {"changes": CHANGES}}))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    out = review_examples.draft(tmp_path)
    assert out.count("(none in the sidecars)") == 2  # no lessons, no follow-ups


def test_this_task_leaves_its_files_neutral() -> None:
    src = ROOT / "plugins/dev-loop/src/dev_loop/records/review_examples.py"
    assert check([src, Path(__file__)]) == []
