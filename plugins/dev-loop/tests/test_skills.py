"""The skills parse, name only subcommands that exist in the `dl` table, stay neutral."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml
from test_neutral import check

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN / "src"))
from dev_loop.__main__ import TABLE  # noqa: E402

SKILLS = sorted((PLUGIN / "skills").glob("*/SKILL.md"))
NEW = [PLUGIN / "skills/build/SKILL.md", PLUGIN / "skills/review/SKILL.md"]


def test_frontmatter_valid() -> None:
    for path in NEW:
        meta = yaml.safe_load(path.read_text(encoding="utf-8").split("---\n")[1])
        assert meta["name"] == path.parent.name
        assert meta["description"]


def test_every_dl_subcommand_named_exists() -> None:
    for path in NEW:
        for sub in re.findall(r"\bdl ([a-z][a-z-]*)", path.read_text(encoding="utf-8")):
            assert sub in TABLE, f"{path.parent.name}: dl {sub}"


def test_build_skill_rules() -> None:
    text = NEW[0].read_text(encoding="utf-8")
    for needle in ("dl tasklist parse", "dl tasklist set", "dl records recall", "dl lesson",
                   "dl config route", "git rev-parse --verify", "--no-verify"):
        assert needle in text, needle


def test_build_skill_sends_a_build_defect_to_design_md() -> None:
    text = " ".join(NEW[0].read_text(encoding="utf-8").split())
    for needle in ("goes in the change's `design.md`", "`dl incident <change> what=",
                   "`dl lesson` is only for a fixed review finding"):
        assert needle in text, needle
    assert "is logged with `dl lesson" not in text


def test_build_skill_forbids_writing_source_by_bash() -> None:
    assert "by Edit or by Bash" in " ".join(NEW[0].read_text(encoding="utf-8").split())


def test_review_skill_lessons_always_and_records_need_no_round() -> None:
    text = " ".join(NEW[1].read_text(encoding="utf-8").split())
    for needle in ("also when it is 0", "need no round", "starts in the project root"):
        assert needle in text, needle


def test_build_skill_holds_the_brief_template_and_subject_rule() -> None:
    text = NEW[0].read_text(encoding="utf-8")
    defaults = yaml.safe_load((PLUGIN / "src/dev_loop/defaults.yaml").read_text(encoding="utf-8"))
    for name in defaults["briefs"]["sections"]:
        assert f"## {name}" in text, name
    assert "exactly `T<n>: <title>`" in text
    assert "`dl config get briefs.sections`" in text


def test_review_skill_rules() -> None:
    text = NEW[1].read_text(encoding="utf-8")
    for needle in ("dl review", "REVIEW_BASE", "dl host threads --json", "dl host thread-reply --json",
                   "dl host thread-status --json", "dl host thread-post --json",
                   "dl record <change> follow-up", "dl record <change> round", "dl lesson <change>",
                   "review.governance", "counts as a major", "two consecutive rounds",
                   "no blocker and no major", "Never approve", "never merge"):
        assert needle in text, needle


def test_review_skill_records_no_round_from_a_failed_review() -> None:
    text = " ".join(NEW[1].read_text(encoding="utf-8").split())
    for needle in ("On a non-zero `dl review` exit, record nothing (no round, no set-status).",
                   "Return `paused` with the FAIL line. Called again, run the same round again.",
                   "one lesson per thread set fixed, including a minor fixed as a major"):
        assert needle in text, needle


def test_review_skill_records_no_round_from_a_failed_review() -> None:
    text = " ".join(NEW[1].read_text(encoding="utf-8").split())
    for needle in ("On a non-zero `dl review` exit, record nothing (no round, no set-status).",
                   "Return `paused` with the FAIL line. Called again, run the same round again.",
                   "one lesson per thread set fixed, including a minor fixed as a major"):
        assert needle in text, needle


def test_done_simplifier_goes_through_a_builder() -> None:
    text = (PLUGIN / "skills/done/SKILL.md").read_text(encoding="utf-8")
    for needle in ("builder", "<slug>: simplify", "dl verify", "never edits code"):
        assert needle in text, needle


def test_review_fix_step_goes_through_a_builder() -> None:
    text = NEW[1].read_text(encoding="utf-8")
    for needle in ("Fixes go through a builder", "never edits product code"):
        assert needle in text, needle


def test_review_skill_fast_track() -> None:
    """V4a: fast track is read from the config, records its debt and says so in the summary;
    v0.4.20 N3: read as the done rule reads it, from the integration branch."""
    text = NEW[1].read_text(encoding="utf-8")
    for needle in ("dl records fast-track", "review.fast_track_fix",
                   "dl record <change> debt", "Fast track: N debt items", "one round"):
        assert needle in text, needle


def test_review_fallback_names_an_existing_agent() -> None:
    for agent in re.findall(r"`dev-loop:([a-z-]+)` agent", NEW[1].read_text(encoding="utf-8")):
        assert (PLUGIN / "agents" / f"{agent}.md").is_file(), agent


def test_review_skill_has_no_project_path_or_credential() -> None:
    text = NEW[1].read_text(encoding="utf-8")
    for banned in (".claude/", "scripts/", "docs/", ".env", "set -a", "AZDO", "_apis/",
                   "Product Owner", "develop"):
        assert banned not in text, banned


def test_new_skills_neutral() -> None:
    assert check(NEW) == []


FORK = {  # F1: heavy skills run in a forked subagent; only their result reaches the caller
    "plan": ("dev-loop:planner", False),
    "research": ("dev-loop:researcher", True),
    "verify": ("general-purpose", False),
    "land": ("general-purpose", True),
}
BUILT_IN = {"general-purpose", "Explore", "Plan"}


def meta_of(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8").split("---\n")[1])


def test_fork_table_holds() -> None:
    for path in SKILLS:
        meta, name = meta_of(path), path.parent.name
        if name not in FORK:
            assert "context" not in meta and "agent" not in meta, f"{name} stays inline"
            continue
        agent, background = FORK[name]
        assert meta["context"] == "fork", name
        assert (meta["agent"], meta["background"]) == (agent, background), name


def test_every_fork_agent_exists() -> None:
    for name in FORK:
        agent = meta_of(PLUGIN / "skills" / name / "SKILL.md")["agent"]
        plugin_agent = agent.removeprefix("dev-loop:")
        assert agent in BUILT_IN or (PLUGIN / "agents" / f"{plugin_agent}.md").is_file(), agent


def test_forked_skills_stand_alone() -> None:
    for name in FORK:
        text = (PLUGIN / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
        assert "AskUserQuestion" not in text, name
        assert "$ARGUMENTS" in text, f"{name} takes its inputs from the arguments"
        for vague in ("as discussed", "the change above", "ask the user", "wait for"):
            assert vague not in text.lower(), f"{name}: {vague}"
        assert "## Return" in text, f"{name} says what to return"


def test_forked_skills_neutral() -> None:
    assert check([PLUGIN / "skills" / name / "SKILL.md" for name in FORK]) == []


def test_plan_skill_wants_acceptance_bullets() -> None:
    text = " ".join((PLUGIN / "skills/plan/SKILL.md").read_text(encoding="utf-8").split())
    assert "`dl review` ticks only bullets" in text
    assert "under `## Acceptance criteria`, one `- ` line each" in text
    assert "naming a command in backticks" in text


def test_review_skill_replies_with_text_and_sets_status_on_the_reviewed_head() -> None:
    text = " ".join(NEW[1].read_text(encoding="utf-8").split())
    fixer = " ".join((PLUGIN / "agents/review-fixer.md").read_text(encoding="utf-8").split())
    fix = ("A finding on a record, report or test file (nothing under the source tree changes) "
           "is fixed by correcting it and running what it claimed. It needs a lesson but no new "
           "test.")
    for needle in ('"text": "<reply>"}', "| dl host thread-reply --json",
                   "`sha` is the `head` of the last `review-run` record, never `git rev-parse HEAD`",
                   "count is the change's total lessons so far", fix):
        assert needle in text, needle
    assert fix in fixer


def test_review_skill_runs_inline_so_it_can_dispatch_fix_builders() -> None:
    text = " ".join(NEW[1].read_text(encoding="utf-8").split())
    assert "context" not in meta_of(NEW[1]) and "agent" not in meta_of(NEW[1])
    assert "Forked" not in text and "forked" not in text
    assert "Dispatch each blocker or major fix" in text


def test_archive_and_done_skills_say_archive_lands_the_move() -> None:
    line = ("run `dl archive <slug>` from the change's worktree. It commits the move and pushes "
            "it to the integration branch itself. Never commit, push, PR or land the move by hand.")
    for name in ("archive", "done"):
        text = " ".join((PLUGIN / "skills" / name / "SKILL.md").read_text("utf-8").split())
        assert f"After `dl land`, {line}" in text


def test_done_skill_never_writes_a_verify_record_by_hand() -> None:
    text = (PLUGIN / "skills/done/SKILL.md").read_text(encoding="utf-8")
    assert "dl record <change> verify" not in text.replace("`dl record ... verify`", "")
    assert "dl verify <change>" in text


def _flat(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())


def test_review_skill_posts_the_local_status_once() -> None:
    text = _flat(NEW[1])
    assert "On the local host" in text
    assert "run it once, before the records push" in text
    assert ("After you push the round's records, run the same `set-status` again with the "
            "same reviewed sha: when") not in text


def test_plan_skill_builds_what_the_approved_proposal_answers() -> None:
    text = _flat(PLUGIN / "skills/plan/SKILL.md")
    assert "is decided: the approval covers it" in text
    assert "`proposal change needed:" in text


def test_review_skill_one_test_per_fixed_thread_and_no_thread_edit() -> None:
    text = _flat(NEW[1])
    assert "a thread that repeats another's defect still gets its own" in text
    assert "Never `dl host thread-edit`" in text


def test_review_skill_pauses_only_behind_cited_words() -> None:
    text = _flat(NEW[1])
    assert "is yours to settle" in text
    assert "dl record <change> pause" in text
    assert "A fix needs a decision that is not yours" not in text


def test_build_skill_gives_lessons_their_own_heading() -> None:
    text = _flat(NEW[0])
    assert "under their own `## Lessons` heading, never under Read first" in text
    assert "## Lessons" in text


def skill_text(name: str) -> str:
    return " ".join((PLUGIN / "skills" / name / "SKILL.md").read_text(encoding="utf-8").split())


def test_pr_skill_says_the_hook_gets_the_target() -> None:
    assert "`PREPUSH_BASE`" in skill_text("pr")
    assert "judges this part alone" in skill_text("pr")


def test_done_skill_names_a_reviewed_pr_per_part() -> None:
    text = skill_text("done")
    assert "the last review round of each part's PR clean" in text
    assert "a reviewed PR for each started part" in text


def test_review_skill_counts_a_lifted_pause_on_the_next_round() -> None:
    text = skill_text("review")
    assert "A paused thread fixed later is counted in the next round's `fixed`." in text
    assert "never run a round only to move a count" in text


def test_review_skill_names_only_the_two_status_states() -> None:
    text = (PLUGIN / "skills/review/SKILL.md").read_text(encoding="utf-8")
    part = text.split("set-status", 1)[1].split("Go again", 1)[0]
    assert "succeeded" not in part
    assert "send `success` or `failure` on every host" in " ".join(part.split())


def test_review_skill_names_the_reviewed_line_on_a_hosted_host() -> None:
    text = skill_text("review")
    for needle in ("a hosted host writes no such record", "`Reviewed:` line",
                   "after each later push that adds only records", '"<the reviewed sha>"'):
        assert needle in text, needle


def test_review_skill_names_the_decisions_key() -> None:
    assert "`knowledge.decisions`" in skill_text("review")


def test_build_skill_says_a_shared_file_waits_a_wave() -> None:
    text = skill_text("build")
    assert "Two tasks that name the same file never share a wave" in text
    assert "each commits only its own files, by path" in text


def test_build_skill_resplits_before_it_asks_for_a_waiver() -> None:
    text = skill_text("build")
    for needle in ("Re-split first", "goes back to `todo`",
                   "only when no arrangement of whole tasks into two parts fits"):
        assert needle in text, needle
    assert text.index("Re-split first") < text.index("Ask for a `Size waiver:` line")


def test_plan_skill_estimates_what_the_cap_counts() -> None:
    assert "docstrings, imports and export lists" in skill_text("plan")


def test_done_skill_judges_a_started_part() -> None:
    assert "every task of each started part" in skill_text("done")
