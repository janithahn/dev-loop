"""The seven agents parse, match the roles in defaults.yaml, and stay neutral."""

from __future__ import annotations

from pathlib import Path

import yaml
from test_neutral import check

PLUGIN = Path(__file__).resolve().parents[1]
FILES = sorted((PLUGIN / "agents").glob("*.md")) + [PLUGIN / "skills/research/SKILL.md"]
MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}


def front(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), path
    return yaml.safe_load(text.split("---\n")[1])


def test_frontmatter_parses_and_names_match_roles() -> None:
    defaults = yaml.safe_load((PLUGIN / "src/dev_loop/defaults.yaml").read_text(encoding="utf-8"))
    types = {t for role in defaults["roles"].values() for t in role["agent_types"]}
    agents = {front(p)["name"]: front(p) for p in FILES if p.parent.name == "agents"}
    assert set(agents) == {"planner", "researcher", "code-reviewer", "builder",
                           "builder-backend", "builder-frontend", "review-fixer"}
    assert {f"dev-loop:{n}" for n in agents} <= types
    for name, meta in agents.items():
        assert meta["description"] and meta["tools"], name
        assert meta["model"] in MODELS, name


def test_neutral() -> None:
    assert check(FILES) == []
    assert not (PLUGIN / "skills/research/agents").exists()


def test_no_builder_rereads_claude_md_or_the_whole_pitfalls_file() -> None:
    for path in FILES:
        if path.name not in {"builder.md", "builder-backend.md", "builder-frontend.md",
                             "review-fixer.md"}:
            continue
        text = " ".join(path.read_text(encoding="utf-8").split())
        assert "already in your context" in text, path.name
        assert "do not read the whole pitfalls file" in text, path.name
        assert "the pitfalls file (`knowledge.pitfalls`)" not in text, path.name


def test_review_fixer_never_holds_a_loop_decision_for_a_person() -> None:
    text = " ".join((PLUGIN / "agents/review-fixer.md").read_text(encoding="utf-8").split())
    assert "never hold a thread for a person to confirm" in text
    assert "dl record <change> pause" in text
    assert "a contract change, a product call, a guardrail" not in text


def test_builders_never_search_random_text_for_a_short_word() -> None:
    for name in ("builder", "builder-backend", "builder-frontend"):
        text = " ".join((PLUGIN / f"agents/{name}.md").read_text(encoding="utf-8").split())
        assert "compare the whole output" in text, name
        assert "random name" in text, name


def test_reviewer_files_a_chance_failing_assertion_as_major() -> None:
    text = " ".join((PLUGIN / "agents/code-reviewer.md").read_text(encoding="utf-8").split())
    assert "random name is a major" in text


def test_builders_commit_their_own_files_by_path() -> None:
    for name in ("builder", "builder-backend", "builder-frontend"):
        text = " ".join((PLUGIN / f"agents/{name}.md").read_text(encoding="utf-8").split())
        assert "by path" in text, name
        assert "never `git add -A`" in text, name


def test_builders_say_which_new_tests_failed_first() -> None:
    for name in ("builder", "builder-backend", "builder-frontend"):
        text = " ".join((PLUGIN / f"agents/{name}.md").read_text(encoding="utf-8").split())
        assert "which new tests failed before the change" in text, name
        assert "is a pin" in text, name


def test_builders_carry_the_area_rules_and_the_spec_wins() -> None:
    needles = ('"Do not repeat" block', "quote the lines you acted on",
               "For every such rule that applies to the diff, name the test",
               "`dl config get gates.on_edit`", "no guard configured",
               "`dl config get knowledge.guides`", "Read further than the brief only for a gap",
               "Read beyond the brief: <path> — <why>", "the spec wins",
               "Never run `dl records approve`", "are not \"beyond the brief\"")
    for name in ("builder", "builder-backend", "builder-frontend"):
        text = " ".join((PLUGIN / f"agents/{name}.md").read_text(encoding="utf-8").split())
        for needle in needles:
            assert needle in text, (name, needle)
