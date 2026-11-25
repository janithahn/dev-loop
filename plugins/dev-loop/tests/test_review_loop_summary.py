"""The review loop's summary is posted as a closed thread.

A host policy that blocks a merge while any thread is active would otherwise hold the PR after
approval. The skill's "When the loop ends" section must post it closed, through `dl host`.
"""

from __future__ import annotations

from pathlib import Path

SKILL = Path(__file__).resolve().parents[1] / "skills" / "review" / "SKILL.md"


def loop_end_section() -> str:
    text = SKILL.read_text(encoding="utf-8")
    start = text.index("## When the loop ends")
    end = text.find("\n## ", start + 1)
    return text[start : end if end != -1 else len(text)]


def test_summary_is_posted_as_a_closed_thread() -> None:
    section = loop_end_section()
    assert "**closed** thread" in section
    assert "`dl host thread-post --json`" in section


def test_summary_names_the_status_fallback() -> None:
    section = loop_end_section()
    assert "`dl host thread-status --json`" in section
    assert '"status": "closed"' in section
