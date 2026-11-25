"""The skill text that bounds a turn: approve runs the plan and ends; the plan commits itself."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import test_neutral  # noqa: E402

PLUGIN = Path(__file__).resolve().parents[1]
FILES = ["skills/propose/SKILL.md", "skills/plan/SKILL.md", "agents/planner.md"]


def text(name: str) -> str:
    return (PLUGIN / name).read_text(encoding="utf-8")


def test_approve_turn_runs_the_plan_and_ends() -> None:
    propose = text("skills/propose/SKILL.md")
    assert "/dev-loop:plan <slug>" in propose and "end the turn" in propose
    assert "/dev-loop:build <slug>" in propose


def test_plan_commits_its_files() -> None:
    assert "<change>: plan" in text("skills/plan/SKILL.md")
    assert "<change>: plan" in text("agents/planner.md")


def test_flow_text_is_neutral() -> None:
    assert test_neutral.check([PLUGIN / f for f in FILES]) == []
