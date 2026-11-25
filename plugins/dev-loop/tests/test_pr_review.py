"""Self-check for pr-review.sh's verdict validation and spec ticks,
and for its refusal of a round that would review the wrong tree (pr-review-head-guard, 2026-10-21).

Run: python -m pytest test_pr_review.py -q   (needs bash, git, jq and a Python with PyYAML)

Each case runs `dl review` (run.py, which execs the real pr-review.sh from the plugin tree) in a
throwaway git repo whose `dev-loop.yaml` overlay names the integration branch `trunk`, the change
folders, the governance, the guides, the pitfalls file and the project's rubric, with
a fake `claude` (canned replies per pass, no model) first on PATH and a fake `dl` (its `host`
verbs answer with the PR's JSON or its threads and keep every write's verb beside its body; every
other subcommand runs the real launcher). A `curl` on PATH records any call: the round makes none. A malformed, empty or unparsable verdict, or one missing a spec line,
must fail the round (exit 1, the summary says INCOMPLETE); a good one ticks the spec in the summary.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import hashlib
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest

CI = Path(__file__).resolve().parent
REVIEW = CI.parent / "src" / "dev_loop" / "review"
DL = CI.parent / "bin" / "dl"
BASH = shutil.which("bash") or "bash"
SPEC = "# Spec\n\n## Acceptance criteria\n- AC-1 the first line\n- AC-2 the second line\n"
FINDING = {"file": "a.py", "line": 1, "severity": "minor", "rule": "x", "message": "m. fix."}
B_REPLY = {"verdict": "APPROVE", "summary": "b", "files_seen": 2, "findings": []}
TICKS = [
    {"id": "S1", "status": "met", "evidence": "a.py:1"},
    {"id": "S2", "status": "failed", "evidence": "no test"},
]
FAILED = {**FINDING, "severity": "major", "rule": "spec-line-failed", "message": "S2 unbuilt."}
GOOD = {**B_REPLY, "verdict": "CHANGES_REQUESTED", "findings": [FINDING, FAILED], "spec": TICKS}

FAKE_CLAUDE = """#!/usr/bin/env bash
echo called >> "$FAKE_DIR/claude_calls.txt"
case "$*" in *"Reply with the single word OK."*) echo '{"is_error":false,"result":"OK"}'; exit 0;; esac
case "$*" in *"Focus ONLY on design"*) pass=b;; *) pass=a;; esac
printf '%s' "$*" > "$FAKE_DIR/args_$pass.txt"
printf '%s\\0' "$@" > "$FAKE_DIR/argv_$pass.bin"
env > "$FAKE_DIR/env_$pass.txt"
prev=""; for a in "$@"; do
  [ "$prev" = --append-system-prompt-file ] && cp "$a" "$FAKE_DIR/system_$pass.txt"; prev="$a"
done
cat > "$FAKE_DIR/stdin_$pass.txt"
cat "$FAKE_DIR/reply_$pass.json"
"""
FAKE_DL = """#!/usr/bin/env bash
[ "$1" = host ] || exec bash "$REAL_DL" "$@"
verb="$2"; body="$(cat)"
[ "$3" = --json ] || { echo "dl host: no --json" >&2; exit 2; }
case "$verb" in
  threads) cat "$FAKE_DIR/threads.json"; exit 0;;
  pr-get) cat "$FAKE_DIR/pr.json"; exit 0;;
esac
printf '%s\t%s\n' "$verb" "$body" >> "$FAKE_DIR/posted.txt"
[ -n "${FAKE_FAIL_WRITES:-}" ] && { echo "dl host $verb: host answered HTTP 500" >&2; exit 1; }
echo '{"id": 99}'
"""
# A runner other than claude (review.runner_command): prints the verdict JSON itself, no envelope.
FAKE_RUNNER = """#!/usr/bin/env bash
while [ $# -gt 0 ]; do
  case "$1" in --model) model="$2";; --prompt-file) prompt="$2";; esac; shift
done
case "$(cat "$prompt")" in *"Focus ONLY on design"*) pass=b;; *) pass=a;; esac
cp "$prompt" "$FAKE_DIR/runner_prompt_$pass.txt"
printf '%s' "$model" > "$FAKE_DIR/runner_model_$pass.txt"
cat > "$FAKE_DIR/runner_stdin_$pass.txt"
cat "$FAKE_DIR/verdict_$pass.json"
"""
FAKE_CURL = """#!/usr/bin/env bash
echo "$*" >> "$FAKE_DIR/curl.txt"
exit 7
"""
HOST_TOKEN = "zz-host-token-zz"


@dataclass(frozen=True)
class Case:
    """One round's tree and PR; the defaults are a PR reviewed from its own head, as the loop runs it."""

    spec: str | bytes | None = SPEC
    commits: bool = True  # False: the branch adds nothing to develop
    base: str | None = None  # REVIEW_BASE
    source: str | None = "HEAD"  # the PR's lastMergeSourceCommit (a rev here); None: unreadable
    merge: str | None = None  # the PR's lastMergeCommit, ADO's merge ref (a rev here)
    threads: tuple[dict[str, object], ...] = ()  # the PR's existing threads
    fix: str = "x = 2\n"  # a.py at the fix commit
    dirty: bool = False  # a tracked file edited after the last commit
    env: tuple[tuple[str, str], ...] = ()
    cwd: str = ""  # the folder of the repo the round is started from (any folder inside it)
    examples_path: str | None = None  # review.examples: a project file of worked examples
    examples_text: str | None = None  # its content; a path with no text = the file is missing
    rules_text: str | None = None  # RULES.md (governance), when a case needs it larger
    target: str | None = None  # a stacked PR: the parent branch, written as targetRefName


# The project's overlay: every review input named, none at a project-specific place.
OVERLAY = """version: 1
paths: {changes: work/changes, archive: work/changes/archive}
branches: {integration: trunk}
knowledge: {pitfalls: notes/PITFALLS.md, guides: ["*/GUIDE.md", "GUIDE.md"]}
review:
  governance: [RULES.md, "rules/*.md"]
  extra: {"work/changes/*": notes/TARGET.md}
  standards: notes/RUBRIC.md
"""
INPUTS = {  # path: a marker the system prompt holds exactly when the file is inlined
    "RULES.md": "marker-rules", "rules/style.md": "marker-style", "notes/TARGET.md": "marker-target",
    "notes/PITFALLS.md": "marker-pitfalls", "notes/RUBRIC.md": "marker-rubric",
    "GUIDE.md": "marker-root-guide", "pkg/GUIDE.md": "marker-pkg-guide",
    "other/GUIDE.md": "marker-other-guide",
}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(  # noqa: S603 - fixed git arguments
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],  # noqa: S607
        cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()


def crid(text: str) -> str:
    """The id pr-review.sh gives a finding: sha1 of the UTF-8 bytes of `file|code`, 12 hex."""
    return hashlib.sha1(text.encode()).hexdigest()[:12]  # noqa: S324 - an id, not a secret


def thread(tid: int, content: str) -> dict[str, object]:
    return {"id": tid, "status": "active", "threadContext": {"filePath": "/a.py"},
            "comments": [{"content": content}]}


def make_repo(root: Path, case: Case) -> Path:
    origin, repo = root / "origin", root / "repo"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "trunk")
    (origin / "base.txt").write_text("base\n", encoding="utf-8")
    overlay = OVERLAY + (f"  examples: {case.examples_path}\n" if case.examples_path else "")
    (origin / "dev-loop.yaml").write_text(overlay, encoding="utf-8")
    for path, marker in INPUTS.items():
        (origin / path).parent.mkdir(parents=True, exist_ok=True)
        (origin / path).write_text(f"{marker}\n", encoding="utf-8")
    if case.examples_path and case.examples_text is not None:
        (origin / case.examples_path).parent.mkdir(parents=True, exist_ok=True)
        (origin / case.examples_path).write_text(case.examples_text, encoding="utf-8")
    if case.rules_text is not None:
        (origin / "RULES.md").write_text(case.rules_text, encoding="utf-8")
    git(origin, "add", ".")
    git(origin, "commit", "-q", "-m", "base")
    if case.target:  # a parent branch holding parent.py; the feature is stacked on it
        git(origin, "checkout", "-q", "-b", case.target)
        (origin / "parent.py").write_text("p = 1\n", encoding="utf-8")
        git(origin, "add", ".")
        git(origin, "commit", "-q", "-m", "parent")
        git(origin, "checkout", "-q", "trunk")
    git(root, "clone", "-q", str(origin), "repo")
    git(repo, "checkout", "-q", "-b", "feature", f"origin/{case.target}" if case.target else "HEAD")
    if case.commits:
        (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
        (repo / "pkg" / "b.py").write_text("y = 1\n", encoding="utf-8")
        if case.spec is not None:
            change = repo / "work" / "changes" / "demo"
            change.mkdir(parents=True)
            spec = case.spec.encode() if isinstance(case.spec, str) else case.spec
            (change / "spec.md").write_bytes(spec)
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "feature")
        (repo / "a.py").write_bytes(case.fix.encode())
        git(repo, "commit", "-q", "-am", "the fix")
    if case.dirty:
        (repo / "base.txt").write_text("edited, not committed\n", encoding="utf-8")
    return repo


def review(reply_a: object, case: Case = Case()) -> tuple[int, str, str, Path]:  # noqa: B008
    """Run one round; return its exit code, stderr, every body it posted, and the fakes' folder."""
    root = Path(tempfile.mkdtemp())
    repo, fakes = make_repo(root, case), root / "fakes"
    fakes.mkdir()
    pr = {} if case.source is None else {
        "lastMergeSourceCommit": {"commitId": git(repo, "rev-parse", case.source)},
        "lastMergeCommit": {"commitId": git(repo, "rev-parse", case.merge) if case.merge else "0" * 40},
    }
    if case.target:
        pr["targetRefName"] = f"refs/heads/{case.target}"
    (fakes / "pr.json").write_text(json.dumps(pr), encoding="utf-8")
    (fakes / "threads.json").write_text(json.dumps(list(case.threads)), "utf-8")
    fakes_run = (("claude", FAKE_CLAUDE), ("curl", FAKE_CURL), ("dl", FAKE_DL))
    for name, text in (*fakes_run, ("myrunner", FAKE_RUNNER)):
        (fakes / name).write_text(text, encoding="utf-8", newline="\n")
        (fakes / name).chmod(0o755)
    result = reply_a if isinstance(reply_a, str) else json.dumps(reply_a)
    (fakes / "reply_a.json").write_text(json.dumps({"result": result}), encoding="utf-8")
    (fakes / "reply_b.json").write_text(json.dumps({"result": json.dumps(B_REPLY)}), "utf-8")
    (fakes / "verdict_a.json").write_text(result, encoding="utf-8")
    (fakes / "verdict_b.json").write_text(json.dumps(B_REPLY), encoding="utf-8")
    inherited = {k: v for k, v in os.environ.items()
                 if k not in ("CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_DATA", "TARGET_BRANCH", "DL")}
    env = {
        **inherited, "DL_PYTHON": sys.executable, "PATH": f"{fakes}{os.pathsep}{os.environ['PATH']}", "FAKE_DIR": str(fakes),
        "CLAUDE_CODE_OAUTH_TOKEN": "zz-test-token-zz", "PR_ID": "1", "PASS_BACKOFF": "0",
        "DL": str(fakes / "dl"), "REAL_DL": str(DL), "DEV_LOOP_HOST_TOKEN": HOST_TOKEN,
        **dict(case.env),
    }
    if case.base:  # a follow-up round: only the commits since the base
        env["REVIEW_BASE"] = case.base
    done = subprocess.run(  # noqa: S603 - the repo's own script, fixed arguments
        [BASH, str(DL), "review"], cwd=repo / case.cwd, env=env, capture_output=True,
        text=True, encoding="utf-8", errors="replace", check=False,
    )
    (fakes / "stdout.txt").write_text(done.stdout, encoding="utf-8")
    posted = fakes / "posted.txt"
    return done.returncode, done.stderr, posted.read_text("utf-8") if posted.exists() else "", fakes


def check_good_verdict_ticks_the_spec() -> None:
    code, err, posted, fakes = review(GOOD)
    assert code == 0, err
    assert "Spec: 1/2 met" in posted
    assert "✅ S1 met: AC-1 the first line — a.py:1" in posted
    assert "❌ S2 failed: AC-2 the second line — no test" in posted
    assert "S1\tAC-1 the first line" in (fakes / "stdin_a.txt").read_text("utf-8")
    assert "DONE-WHEN" not in (fakes / "stdin_b.txt").read_text("utf-8")  # pass A ticks, B not


def check_follow_up_ticks_against_head() -> None:
    code, err, posted, fakes = review(GOOD, Case(base="HEAD~1"))
    assert code == 0, err
    assert "Spec: 1/2 met" in posted
    assert "files at HEAD" in (fakes / "args_a.txt").read_text("utf-8")  # ticks judged on HEAD


def check_failed_tick_without_a_major_fails_the_round() -> None:
    check_bad_verdict_fails_the_round({**GOOD, "findings": [FINDING]}, "S2 ticked failed")


def check_no_spec_is_stated_not_ticked() -> None:
    code, err, posted, _ = review({k: v for k, v in GOOD.items() if k != "spec"}, Case(spec=None))
    assert code == 0, err
    assert "no done-when lines found" in posted


def check_bad_verdict_fails_the_round(
    reply_a: object, fault: str, case: Case = Case()  # noqa: B008
) -> None:
    code, err, posted, _ = review(reply_a, case)
    assert code == 1, (fault, err)
    assert fault in err, (fault, err)
    assert "INCOMPLETE" in posted
    assert "2 done-when line(s) not ticked" in posted
    assert "✅ S1" not in posted
    assert "m. fix." not in posted  # an off-schema reply posts none of its findings


def check_refused(case: Case, cause: str) -> tuple[str, Path]:
    """A round on the wrong tree, or with nothing to review, fails and posts a skip notice only."""
    code, err, posted, fakes = review(GOOD, case)
    assert code == 1, (cause, err)
    assert cause in err, (cause, err)
    assert "SKIPPED" in posted and "`crid:skip`" in posted, posted
    assert "Claude PR review (advisory)" not in posted  # never a clean summary
    assert '"status":"fixed"' not in posted  # no thread resolved
    assert posted.startswith("thread-post\t")  # a new notice is a PR-level thread
    assert '"file"' not in posted
    assert not (fakes / "stdin_a.txt").exists()  # the model never ran
    return err, fakes.parent / "repo"


def check_wrong_head_is_refused() -> None:  # audit B1
    old = thread(77, "🟠 **major** — an earlier finding.\n\n`crid:aaaaaaaaaaaa`")
    err, repo = check_refused(Case(source="HEAD~1", threads=(old,)), "is not the PR's pushed head")
    head, source = git(repo, "rev-parse", "HEAD"), git(repo, "rev-parse", "HEAD~1")
    assert f"HEAD {head}" in err and f"PR source {source}" in err, err  # names both shas
    check_refused(Case(source=None), "could not read the PR's pushed head")
    check_refused(Case(dirty=True), "tracked files are edited")


def check_the_reviewed_head_is_printed() -> None:  # v0.4.16 F1
    code, err, _, fakes = review(GOOD)
    assert code == 0, err
    lines = (fakes / "stdout.txt").read_text("utf-8").splitlines()
    named = [x for x in lines if x.startswith("Reviewed:")]
    assert len(named) == 1 and re.fullmatch(r"Reviewed: [0-9a-f]{40}", named[0]), lines
    assert named[0] == f"Reviewed: {git(fakes.parent / 'repo', 'rev-parse', 'HEAD')}"
    assert lines.index(named[0]) < next(i for i, x in enumerate(lines) if x.startswith("Usage:"))


def check_a_refused_round_names_no_reviewed_head() -> None:  # a pin: passes before F1 too
    code, _, _, fakes = review(GOOD, Case(source="HEAD~1"))
    assert code != 0
    assert "Reviewed:" not in (fakes / "stdout.txt").read_text("utf-8")


def check_ci_merge_ref_is_reviewed() -> None:  # the CI pipeline runs on ADO's merge ref
    code, err, posted, _ = review(GOOD, Case(source="HEAD~1", merge="HEAD"))
    assert code == 0, err
    assert "Spec: 1/2 met" in posted


def check_empty_diff_fails_the_round() -> None:  # audit B1 (on develop) and B2 (follow-up)
    check_refused(Case(commits=False, spec=None), "no diff vs trunk")
    check_refused(Case(base="HEAD"), "no change to the PR's files since HEAD")


def check_crid_is_the_footer_only() -> None:  # audit M1
    flagged = crid("a.py|x")  # FINDING: line 1, so the rule stands in for the code
    old = thread(77, f"🟠 **major** — the `crid:skip` thread is resolved early.\n\n`crid:{flagged}`")
    code, err, posted, _ = review(GOOD, Case(threads=(old,)))
    assert code == 0, err
    assert '"thread":"77"' not in posted  # still flagged: left open, never closed as the skip notice
    assert "m. fix." not in posted  # and not posted again as a duplicate
    code, err, posted, _ = review(GOOD, Case(base="no-such-commit", threads=(old,)))
    assert code == 1, err
    assert '"thread":"77"' not in posted  # the skip notice never overwrites a finding
    assert "`crid:skip`" in posted


def check_crid_hashes_utf8_bytes() -> None:  # audit M2: Windows read stdin as cp1252
    line = "s = ”quoted”"
    reply = {**GOOD, "findings": [{**FINDING, "line": 2}, FAILED]}
    case = Case(fix=f"x = 2\n{line}\n", env=(("PYTHONIOENCODING", "cp1252"),))
    code, err, posted, _ = review(reply, case)
    assert code == 0, err
    assert f"`crid:{crid('a.py|' + line)}`" in posted, posted


def check_unread_spec_is_not_run() -> None:  # audit M3
    reply = {k: v for k, v in GOOD.items() if k != "spec"}
    for spec, cause in (("# Spec\n\n## Acceptance\nThe page shows the case.\n", "no done-when"),
                        (b"\xff\xfe not utf-8", "UnicodeDecodeError")):
        code, err, posted, _ = review(reply, Case(spec=spec))
        assert code == 1, (cause, err)
        assert "Spec: not_run" in posted and cause in posted, posted
        assert "no done-when lines found" not in posted


def check_every_host_call_goes_through_the_adapter() -> None:  # no curl, no token
    skip = thread(5, "🤖 ⚠️ **Claude PR review SKIPPED** — earlier.\n\n`crid:skip`")
    for case in (Case(base="HEAD~1"), Case(source="HEAD~1", threads=(skip,)), Case(source=None)):
        _, err, _, fakes = review(GOOD, case)  # every GET, the writes, the notice's edit and post
        assert not (fakes / "curl.txt").exists(), (case, err)
    code, err, _, _ = review(GOOD, Case(source=None, env=(("FAKE_FAIL_WRITES", "1"),)))
    assert code == 1 and "the SKIPPED notice could not be posted" in err, err  # never silent
    code, err, _, _ = review(GOOD, Case(env=(("FAKE_FAIL_WRITES", "1"),)))
    assert code == 1 and "write(s) failed" in err, err  # a lost finding fails the round
    script = (REVIEW / "pr-review.sh").read_text("utf-8")
    for gone in ("curl", "ADO_", "ACCESSTOKEN", "Authorization"):
        assert gone not in script, gone


def check_the_skip_notice_is_edited_in_place() -> None:  # one `crid:skip` thread, never two
    skip = thread(5, "🤖 ⚠️ **Claude PR review SKIPPED** — earlier.\n\n`crid:skip`")
    _, _, posted, _ = review(GOOD, Case(source="HEAD~1", threads=(skip,)))
    assert posted.startswith('thread-edit\t{"pr":"1","thread":"5","text":'), posted
    assert "thread-post" not in posted


def check_findings_post_resolve_and_reply() -> None:  # anchored post; a gone finding resolved
    old = thread(41, "🟠 **major** — gone now.\n\n`crid:bbbbbbbbbbbb`")
    code, err, posted, fakes = review(GOOD, Case(threads=(old,)))
    assert code == 0, err
    posts = [json.loads(line.split("\t", 1)[1]) for line in posted.splitlines()
             if line.startswith("thread-post\t")]
    assert {"pr": "1", "file": "a.py", "line": 1} == {k: posts[0][k] for k in ("pr", "file", "line")}
    assert '"thread":"41","status":"fixed"' in posted, posted
    assert 'thread-reply\t{"pr":"1","thread":"41","text":"✅ No longer flagged' in posted, posted
    assert '"thread":"99","status":"closed"' in posted  # the new summary thread is closed
    assert HOST_TOKEN not in (fakes / "env_a.txt").read_text("utf-8")  # the model never holds it


def check_one_thread_per_crid_the_higher_severity() -> None:
    major = {**FINDING, "severity": "major", "message": "m2. fix."}
    reply = {**B_REPLY, "verdict": "CHANGES_REQUESTED", "findings": [FINDING, major]}
    _, _, posted, _ = review(reply, Case(spec=None))
    posts = [line for line in posted.splitlines()
             if line.startswith("thread-post	") and "crid:summary" not in line]
    assert len(posts) == 1 and "**major**" in posts[0], posted


def check_the_reviewer_budget_comes_from_the_config() -> None:
    config = Path(tempfile.mkdtemp())
    (config / "dev-loop.yaml").write_text("roles: {reviewer: {model: m-c, max_turns: 7, usd: 2.5}}")
    code, err, _, fakes = review(GOOD, Case(env=(("REVIEW_CONFIG_ROOT", str(config)),)))
    args = (fakes / "args_a.txt").read_text("utf-8") + " "  # the last flag ends the argv
    assert code == 0 and "--model m-c " in args, (err, args)
    assert "--max-turns 7 --max-budget-usd 2.5 " in args, args


def runner_config(review_keys: str) -> Case:
    """A round whose trusted config (REVIEW_CONFIG_ROOT) sets `review: {review_keys}`."""
    config = Path(tempfile.mkdtemp())
    (config / "dev-loop.yaml").write_text(f"review: {{{review_keys}}}\n", encoding="utf-8")
    return Case(env=(("REVIEW_CONFIG_ROOT", str(config)),))


CUSTOM = runner_config(
    "runner: myrunner, model: m-x,"
    " runner_command: 'myrunner --model {model} --prompt-file {prompt}'"
)


def check_claude_runner_keeps_todays_argv() -> None:  # B6: byte-for-byte today's invocation
    code, err, _, fakes = review(GOOD)
    assert code == 0, err
    argv = (fakes / "argv_a.bin").read_bytes().decode("utf-8").split("\0")[:-1]
    assert argv[0] == "-p" and argv[1].startswith(" Conduct the complete, thorough review"), argv
    assert argv[1].endswith("Return ONLY the JSON object from the output contract."), argv
    system = argv.index("--append-system-prompt-file") + 1
    assert argv[2:system] == [
        "--model", "claude-opus-5-5", "--settings", '{"disableAllHooks": true}',
        "--append-system-prompt-file",
    ], argv
    assert argv[system + 1:] == [
        "--allowedTools", "Read,Grep,Glob", "--disallowedTools", "Read(~/.claude/**)",
        "--permission-mode", "dontAsk", "--output-format", "json", "--max-turns", "12",
        "--max-budget-usd", "6",
    ], argv
    code, err, _, fakes = review(GOOD, runner_config("runner: claude, model: m-y"))
    assert code == 0 and "--model m-y " in (fakes / "args_a.txt").read_text("utf-8"), err


def check_custom_runner_gets_the_prompt_file() -> None:  # B6: review.runner_command
    code, err, posted, fakes = review(GOOD, CUSTOM)
    assert code == 0, err
    assert "Spec: 1/2 met" in posted and "m. fix." in posted  # its verdict is read
    assert not (fakes / "claude_calls.txt").exists()  # no claude call, not even the login probe
    assert (fakes / "runner_model_a.txt").read_text("utf-8") == "m-x"
    prompt = (fakes / "runner_prompt_a.txt").read_text("utf-8")
    assert "marker-rubric" in prompt and "spec-line-failed" in prompt  # the system prompt
    assert "Return ONLY the JSON object" in prompt and "=== PR DIFF" in prompt  # the ask, the diff
    assert "S1\tAC-1 the first line" in prompt
    assert HOST_TOKEN not in prompt


def check_custom_runner_invalid_verdict_is_incomplete() -> None:  # B6
    check_bad_verdict_fails_the_round("", "no JSON in model output", CUSTOM)
    check_bad_verdict_fails_the_round({**GOOD, "spec": TICKS[:1]}, "are not S1..S2", CUSTOM)


def test_prompts_and_base_standards() -> None:  # neutral, and the contracts are kept
    from test_neutral import check

    base = (REVIEW / "standards.base.txt").read_text("utf-8")
    assert not (REVIEW / "reviewer-standards.txt").exists()
    assert check([REVIEW / n for n in ("REVIEW-PROMPT.md", "FIX-PROMPT.md", "standards.base.txt")]) == []
    for text in ('"verdict":"APPROVE|CHANGES_REQUESTED"', "files_seen", "spec-line-failed",
                 "Unneeded code is a MAJOR", "swallowed failure is a MAJOR", "weakened"):
        assert text in base, text
    prompt = (REVIEW / "REVIEW-PROMPT.md").read_text("utf-8")
    assert "`crid:<hash>`" in prompt and "sha1(" in prompt and "Never put a literal `crid:`" in prompt


def test_base_examples_parse_as_valid_findings() -> None:  # E1: every worked example is a finding
    from dev_loop.review.review_verdict import finding_errors

    base = (REVIEW / "standards.base.txt").read_text("utf-8")
    assert "One problem, one finding" in base
    section = base.split("# Worked examples", 1)
    assert len(section) == 2, "standards.base.txt has no Worked examples section"
    objects = [json.loads(line) for line in section[1].splitlines() if line.startswith("{")]
    assert len(objects) == 4, objects  # a blocker, a major, a minor and a not-filed borderline
    assert [o["severity"] for o in objects] == ["blocker", "major", "minor", "minor"]
    for n, obj in enumerate(objects):
        assert finding_errors(f"example[{n}]", obj) == [], (n, obj)


def check_context_comes_from_the_config() -> None:  # governance, guides, pitfalls, rubric
    code, err, _, fakes = review(GOOD)
    system = (fakes / "system_a.txt").read_text("utf-8")
    assert code == 0, err
    for path, marker in INPUTS.items():
        assert (marker in system) == (path != "other/GUIDE.md"), (path, system)
    assert system.index("spec-line-failed") < system.index("marker-rubric")  # base, then rubric
    assert system.index("marker-rubric") < system.index("Repo governance")
    assert "----- pkg/GUIDE.md -----" in system
    _, _, _, fakes = review({k: v for k, v in GOOD.items() if k != "spec"}, Case(spec=None))
    assert "marker-target" not in (fakes / "system_a.txt").read_text("utf-8")  # no change touched


def check_project_examples_are_appended() -> None:  # E1: review.examples after the base examples
    case = Case(examples_path="notes/EXAMPLES.md", examples_text="marker-project-examples\n")
    code, err, _, fakes = review(GOOD, case)
    system = (fakes / "system_a.txt").read_text("utf-8")
    assert code == 0, err
    assert "Worked examples" in system  # the base examples are in the standards
    assert "Project review examples" in system and "marker-project-examples" in system
    assert system.index("Worked examples") < system.index("marker-project-examples")  # after


def check_missing_examples_file_is_stated() -> None:  # E1: named but absent → "not run", goes on
    case = Case(examples_path="notes/EXAMPLES.md")  # named in config, the file not written
    code, err, _, fakes = review(GOOD, case)
    system = (fakes / "system_a.txt").read_text("utf-8")
    assert code == 0, err
    assert "not run: review.examples file not found" in system
    assert "marker-project-examples" not in system


def check_a_prompt_over_400_kb_warns_and_goes_on() -> None:  # v0.4.11 F9
    code, err, _, _ = review(GOOD, Case(rules_text="x" * 500_000))
    assert code == 0, err
    assert "WARN: the reviewer's system prompt is" in err and "review.extra" in err
    code, err, _, _ = review(GOOD)
    assert "WARN: the reviewer's system prompt" not in err


def check_root_is_found_from_any_folder() -> None:  # never the script's own place
    for case in (Case(cwd="pkg"), Case(env=(("TARGET_BRANCH", "refs/heads/trunk"),))):
        code, err, posted, _ = review(GOOD, case)
        assert code == 0, (case, err)
        assert "Spec: 1/2 met" in posted


def check_a_stacked_pr_is_diffed_against_its_target() -> None:  # v0.4.13 F3
    code, err, _, fakes = review(GOOD, Case(target="parent"))
    assert code == 0, err
    sent = (fakes / "stdin_a.txt").read_text("utf-8")
    assert "a.py" in sent and "parent.py" not in sent


CHECKS = [
    check_a_stacked_pr_is_diffed_against_its_target,
    check_one_thread_per_crid_the_higher_severity,
    check_the_reviewer_budget_comes_from_the_config, check_every_host_call_goes_through_the_adapter,
    check_the_skip_notice_is_edited_in_place, check_findings_post_resolve_and_reply,
    check_wrong_head_is_refused, check_the_reviewed_head_is_printed,
    check_a_refused_round_names_no_reviewed_head, check_ci_merge_ref_is_reviewed, check_empty_diff_fails_the_round,
    check_crid_is_the_footer_only, check_crid_hashes_utf8_bytes, check_unread_spec_is_not_run,
    check_good_verdict_ticks_the_spec, check_no_spec_is_stated_not_ticked,
    check_follow_up_ticks_against_head, check_failed_tick_without_a_major_fails_the_round,
    check_context_comes_from_the_config, check_root_is_found_from_any_folder,
    check_a_prompt_over_400_kb_warns_and_goes_on,
    check_project_examples_are_appended, check_missing_examples_file_is_stated,
    check_claude_runner_keeps_todays_argv, check_custom_runner_gets_the_prompt_file,
    check_custom_runner_invalid_verdict_is_incomplete,
]
BAD_VERDICTS = [
    ({**GOOD, "findings": [{k: v for k, v in FINDING.items() if k != "severity"}]},
     "schema: findings[0].severity"),
    ("", "no JSON in model output"),
    ("I could not review this {", "parse failed"),
    ({**GOOD, "spec": TICKS[:1]}, "are not S1..S2"),
]


@pytest.mark.parametrize("check", CHECKS, ids=lambda check: check.__name__)
def test_round(check: object) -> None:
    check()  # type: ignore[operator]


@pytest.mark.parametrize(("reply", "fault"), BAD_VERDICTS, ids=[f for _, f in BAD_VERDICTS])
def test_bad_verdict_fails_the_round(reply: object, fault: str) -> None:
    check_bad_verdict_fails_the_round(reply, fault)


def test_context_unit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: object) -> None:
    from dev_loop.review import run

    (tmp_path / "dev-loop.yaml").write_text(OVERLAY, encoding="utf-8")
    (tmp_path / "RULES.md").write_text("marker-rules\n", encoding="utf-8")  # the rest are missing
    text = run.context(tmp_path, ["pkg/b.py"])
    assert "marker-rules" in text and "spec-line-failed" in text
    assert "notes/RUBRIC.md: missing" in text  # a named rubric that is absent is said, not skipped
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    assert run.main(["context", "a.py"]) == 0
    (tmp_path / "dev-loop.yaml").write_text("review: {nope: 1}\n", encoding="utf-8")
    assert run.main(["context", "a.py"]) == 1  # a refused overlay stops the round, never defaults


def test_context_holds_the_nearest_area_guide_of_each_touched_file(tmp_path: Path) -> None:
    from dev_loop.review import run

    (tmp_path / "dev-loop.yaml").write_text("version: 1\n", encoding="utf-8")  # no guides named
    (tmp_path / "pkg/a").mkdir(parents=True)
    (tmp_path / "pkg/a/CLAUDE.md").write_text("trap-a-guide\n", encoding="utf-8")
    (tmp_path / "pkg/AGENTS.md").write_text("trap-pkg-guide\n", encoding="utf-8")
    (tmp_path / "pkg/huge").mkdir()
    (tmp_path / "pkg/huge/CLAUDE.md").write_text("x" * 50000, encoding="utf-8")
    text = run.context(tmp_path, ["pkg/a/x.py", "pkg/a/y.py", "pkg/b/z.py"])
    assert text.count("trap-a-guide") == 1  # nearest guide, once for two files
    assert "trap-pkg-guide" in text  # pkg/b walks up to pkg/AGENTS.md
    big = run.context(tmp_path, ["pkg/huge/q.py"])
    assert "[guide truncated at" in big and big.count("x") < 30000  # a marker, never silent


def test_files_written_are_neutral() -> None:
    from test_neutral import check

    written = [REVIEW / "pr-review.sh", REVIEW / "run.py", CI / "test_pr_review.py"]
    assert check(written) == []


def main() -> None:
    for check in CHECKS:
        check()
    for reply, fault in BAD_VERDICTS:
        check_bad_verdict_fails_the_round(reply, fault)
    print("pr-review.sh verdict checks: ok")


if __name__ == "__main__":
    main()
