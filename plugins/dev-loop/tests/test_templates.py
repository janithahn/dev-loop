"""The templates parse, hold the task block and carry no project, company or contact names."""
import re
from pathlib import Path

import pytest
import yaml
from test_neutral import check

ROOT = Path(__file__).resolve().parents[1] / "templates"
LISTED = [
    "change/proposal.md", "change/spec.md", "change/design.md", "change/tasks.md",
    "change/status.md", "change/verify-report.md", "dev-loop.yaml", "CLAUDE.md", "AGENTS.md", "RULES.md",
    "knowledge/REVIEW-PITFALLS.md", "knowledge/DECISIONS.md",
]


@pytest.mark.parametrize("name", LISTED)
def test_template_is_neutral(name: str) -> None:
    assert check([ROOT / name]) == []


def test_config_parses() -> None:
    data = yaml.safe_load((ROOT / "dev-loop.yaml").read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert {"host", "branches", "paths"} <= set(data)


def test_tasks_has_i4_block() -> None:
    text = (ROOT / "change/tasks.md").read_text(encoding="utf-8")
    assert re.search(r"^### T\d+: .+\n- Status: (todo|doing|done|blocked)\n", text, re.MULTILINE)
    assert "- Files:" in text


def test_no_old_command_name() -> None:
    """Commands are `dl <cmd>`; the old `loop <cmd>` name stays out of templates and prompts."""
    review = Path(__file__).resolve().parents[1] / "src/dev_loop/review"
    files = [*ROOT.rglob("*.md"), *review.glob("*.md")]
    named = [f"{f.name}:{n}" for f in files
             for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1)
             if re.search(r"`loop [a-z]", line)]
    assert named == []


def test_rules_name_the_configured_knowledge_files() -> None:
    text = " ".join((ROOT / "RULES.md").read_text(encoding="utf-8").split())
    assert "`dl config get knowledge.decisions`" in text
    assert "`dl config get knowledge.pitfalls`" in text
    assert "DECISIONS.md" not in text
    assert "REVIEW-PITFALLS.md" not in text
