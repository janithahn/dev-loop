"""`dl pr` opens no PR without a simplifier record since the last review round; stop points."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from test_pr import Fake, fake  # noqa: F401 - fake is the fixture

from dev_loop import pr
from dev_loop.records import record

PLUGIN = Path(__file__).resolve().parents[1]
Err = pytest.CaptureFixture[str]


def folder(root: Path) -> Path:
    return root / ".dev-loop" / "changes" / "c1"


def drop_simplifier(root: Path) -> None:
    path = folder(root) / "records.jsonl"
    rows = [x for x in path.read_text(encoding="utf-8").splitlines()
            if json.loads(x)["kind"] != "simplifier"]
    path.write_text("".join(f"{x}\n" for x in rows), encoding="utf-8")


def a_round(root: Path) -> None:
    record.append(folder(root), "c1", {
        "kind": "round", "pr": 1, "round": 1, "base": "abc", "blocker": 0, "major": 0,
        "minor": 0, "fixed": 0, "deferred": 0, "active": 0})


def refused(stub: Fake, capsys: Err) -> None:
    assert pr.main(["c1"]) == 2
    err = capsys.readouterr().err
    assert "dl pr: no simplifier record since the last review round" in err
    assert "c1: simplify" in err
    assert "dl record c1 simplifier edits=" in err
    assert "dl verify" in err
    assert stub.created == []
    assert not [c for c in stub.commands if c[:2] in (["git", "push"], ["git", "fetch"])]


def test_no_simplifier_record_opens_no_pr(fake: Fake, tmp_path: Path, capsys: Err) -> None:
    drop_simplifier(tmp_path)
    refused(fake, capsys)


def test_a_simplifier_before_the_last_round_is_refused(
    fake: Fake, tmp_path: Path, capsys: Err,
) -> None:
    a_round(tmp_path)
    refused(fake, capsys)


def test_a_simplifier_after_the_last_round_opens(fake: Fake, tmp_path: Path) -> None:
    a_round(tmp_path)
    record.append(folder(tmp_path), "c1", {"kind": "simplifier", "edits": "two names"})
    assert pr.main(["c1"]) == 0
    assert len(fake.created) == 1


def test_an_open_pr_is_never_refused_for_it(fake: Fake, tmp_path: Path) -> None:
    drop_simplifier(tmp_path)
    fake.existing = 7
    assert pr.main(["c1"]) == 0
    assert fake.created == []


def text(relative: str) -> str:
    return (PLUGIN / relative).read_text(encoding="utf-8")


def test_the_build_simplifies_then_verifies_before_its_report() -> None:
    build = text("skills/build/SKILL.md")
    step = build.index("**Simplify, then verify.**")
    assert step < build.index("dl verify <change>", step) < build.index("**Report.**")
    assert "dl record <change> simplifier edits=" in build


@pytest.mark.parametrize("relative", [
    "skills/build/SKILL.md", "skills/pr/SKILL.md", "agents/builder.md",
    "agents/builder-backend.md", "agents/builder-frontend.md"])
def test_turns_have_a_written_stop_point(relative: str) -> None:
    assert re.search(r"^\s*(- )?Stop point:", text(relative), re.MULTILINE)


def test_done_never_simplifies_after_a_review_round() -> None:
    done = text("skills/done/SKILL.md")
    assert re.search(r"Never[^.]*after a review round", done)
    assert "1. Dispatch the simplifier" not in done
