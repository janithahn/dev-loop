"""Acceptance batch 5 (Records and the done rule): each scriptable row of the test plan, as a user.

Every row drives `dl` through its launcher in a throwaway HOME, config dir, plugin data dir and git
config, on a change made by `dl propose` against a bare repo as the hub (F-CHG; harness.py). A row
that fails on today's code is a strict xfail naming its AT id, so the suite stays green and a fix
flips it. Rows the existing unit tests already pin are not repeated here.

Run: python -m pytest plugins/dev-loop/tests/acceptance -q
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

import pytest
from harness import PLUGIN, TIMEOUT, Run, clean_run, f_chg, files

ROUND = ["pr=7", "round=1", "base=full", "blocker=0", "major=0", "minor=0", "fixed=0",
         "deferred=0", "active=0", "turns=1", "seconds=1", "tokens_in=1", "tokens_out=1",
         "unmetered=0"]
METRICS = ["pr=7", "change=demo", "rounds=1", "per_round=0/0/0", "total=0/0/0", "lessons=0"]
DONE = [["verify", "result=green", "head=HEAD"], ["simplifier", "edits=none"],
        ["skills", "skills=tdd"], ["lessons-logged", "count=0"]]
# One good record of each kind the writer knows, its fields given as a user would type them.
KINDS = {
    "round": ROUND, "metrics": METRICS,
    "follow-up": ["pr=7", "thread=70", "file=a.py", "finding=f", "why=w"],
    "debt": ["pr=7", "thread=71", "file=a.py", "severity=minor", "finding=f", "why=w"],
    "lesson": ["priority=high", "summary=the cause", "avoid=do it", "class=new: x",
               "guard=open", "files=README.md", "round=1"],
    "lessons-logged": ["count=1"], "verify": ["result=green", "head=HEAD"],
    "simplifier": ["edits=none"], "skills": ["skills=tdd"], "archive": ["from=a", "to=b"],
    "budget": ["role=builder", "remains=r"],
    "models": ["pr=7", "builder=m1", "reviewer=m2"],
    "skill": ["skill=tdd", "place=p", "pr=7", "by=owner"],
    "handover": ["role=builder", "agent=a1", "context=10", "commit=abc", "done=d", "next=n"],
    "context": ["pr=7", "peak=10", "handovers=0", "stops=0"],
    "recall": ["change=demo", "shown=none"], "rework": ["pr=8", "cause_pr=7", "note=n"],
    "incident": ["what=w", "impact=i", "root_cause=r", "guard=g"],
}


@pytest.fixture
def user(tmp_path: Path) -> Run:
    return Run(tmp_path)


@pytest.fixture
def work(user: Run) -> Path:
    """F-CHG's change worktree, with a ticked task list and a verify report the done rule reads."""
    _, work = f_chg(user)
    folder = changes(work)
    (folder / "tasks.md").write_text("- [x] `T1` done\n", encoding="utf-8")
    (folder / "verify-report.md").write_text("green\n", encoding="utf-8")
    clean_run(user, work)
    return work


def changes(work: Path) -> Path:
    return work / ".dev-loop" / "changes" / "demo"


def sidecar(work: Path) -> list[dict[str, object]]:
    text = (changes(work) / "records.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


def record(user: Run, work: Path, kind: str, *pairs: str) -> subprocess.CompletedProcess[str]:
    return user.dl(work, "record", "demo", kind, *pairs)


def finish(user: Run, work: Path) -> None:
    """Every line of the done rule, through the writer."""
    for kind, *pairs in [["round", *ROUND], ["metrics", *METRICS], *DONE]:
        assert record(user, work, kind, *pairs).returncode == 0, kind


def test_r1_each_kind_written_to_both_files(user: Run, work: Path) -> None:
    for kind, pairs in KINDS.items():
        done = record(user, work, kind, *pairs)
        assert (done.returncode, done.stderr) == (0, ""), (kind, done)
        line = sidecar(work)[-1]["line"]
        assert line == done.stdout.strip(), (kind, line, done.stdout)
    assert [r["kind"] for r in sidecar(work)] == list(KINDS)
    every = "".join(p.read_text(encoding="utf-8") for p in changes(work).glob("*.md"))
    assert all(str(r["line"]) in every for r in sidecar(work))
    assert user.dl(work, "ci", "unparsed").returncode == 0


@pytest.mark.parametrize(("change", "kind", "pairs", "said"), [
    ("demo", "skills", [], "missing skills"),
    ("demo", "skills", ["skills=tdd", "colour=red"], "unknown field colour"),
    ("demo", "follow-up", [*KINDS["follow-up"][:3], "finding=a | b", "why=w"], "bad finding"),
    ("../demo", "skills", ["skills=tdd"], "no change"),
    ("demo/../demo", "skills", ["skills=tdd"], "no change"),
])
def test_r1_bad_record_refused_nothing_written(
    user: Run, work: Path, change: str, kind: str, pairs: list[str], said: str,
) -> None:
    before = files(work / ".dev-loop")
    done = user.dl(work, "record", change, kind, *pairs)
    assert done.returncode == 1 and "refused" in done.stderr and said in done.stderr, done
    assert files(work / ".dev-loop") == before


def test_r2_hand_written_lines_named_and_not_read(user: Run, work: Path) -> None:
    finish(user, work)
    with (changes(work) / "status.md").open("a", encoding="utf-8") as status:
        status.write("\nReview round 2 (PR 7, base x): 1 blocker, 0 major, 0 minor filed\n")
    with (changes(work) / "records.jsonl").open("a", encoding="utf-8") as jsonl:
        jsonl.write("{not json\n")
    found = user.dl(work, "ci", "unparsed")
    assert found.returncode == 1, found
    assert "status.md: no record: Review round 2" in found.stdout, found.stdout
    assert "records.jsonl:7:" in found.stdout, found.stdout
    done = user.dl(work, "record", "done")
    assert (done.returncode, done.stdout) == (0, ""), done


def test_r3_local_hex_pr_id_round_trips(user: Run, work: Path) -> None:
    done = record(user, work, "metrics", *METRICS[1:], "pr=c2425df1f07d")
    assert done.returncode == 0, done
    assert sidecar(work)[-1]["pr"] == "c2425df1f07d"
    assert done.stdout.startswith("| c2425df1f07d |"), done.stdout


def test_r3_all_digit_id_with_leading_zero_round_trips(user: Run, work: Path) -> None:
    done = record(user, work, "metrics", *METRICS[1:], "pr=012345678901")
    assert done.returncode == 0, done
    assert str(sidecar(work)[-1]["pr"]) == "012345678901", sidecar(work)[-1]
    assert done.stdout.startswith("| 012345678901 |"), done.stdout


def test_r4_rerun_written_once_in_each_file(user: Run, work: Path) -> None:
    runs = [record(user, work, "round", *ROUND) for _ in range(2)]
    assert [r.returncode for r in runs] == [0, 0], runs
    assert "already on file" in runs[1].stderr, runs[1]
    assert len(sidecar(work)) == 1
    status = (changes(work) / "status.md").read_text(encoding="utf-8")
    assert status.count("Review round 1 (PR 7") == 1, status


def test_r5_twenty_parallel_writers_lose_nothing(user: Run, work: Path) -> None:
    argv = ["bash", str(PLUGIN / "bin" / "dl"), "record", "demo", "budget", "role=builder"]
    procs = [subprocess.Popen([*argv, f"remains=r{n}"], cwd=work, env=user.env, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE) for n in range(20)]
    outcomes = [(p.communicate(timeout=TIMEOUT), p.returncode) for p in procs]
    assert [code for _, code in outcomes] == [0] * 20, outcomes
    assert sorted(r["remains"] for r in sidecar(work)) == sorted(f"r{n}" for n in range(20))
    status = (changes(work) / "status.md").read_text(encoding="utf-8")
    assert status.count("budget_exhausted: builder;") == 20, status
    assert user.dl(work, "ci", "unparsed").returncode == 0


def test_r6_held_lock_is_one_line_not_a_traceback(user: Run, work: Path) -> None:
    (changes(work) / ".record.lock").write_text("1 held", encoding="utf-8")
    for argv in (["record", "demo", "skills", "skills=tdd"], ["verify", "demo"]):
        start = time.monotonic()
        done = user.dl(work, *argv)
        assert time.monotonic() - start < 30, argv
        assert done.returncode != 0 and "Traceback" not in done.stderr, (argv, done.stderr)
        assert len(done.stderr.strip().splitlines()) == 1 and ".record.lock" in done.stderr, done


CRASH = """
import os, sys
from dev_loop.__main__ import main
from dev_loop.records import record
real = record.replace_text
def crash(path, text):
    real(path, text)
    os._exit(9)
record.replace_text = crash
sys.exit(main(sys.argv[1:]))
"""


def crash_after_markdown(user: Run, work: Path) -> None:
    """Kill the writer after its Markdown write, before its sidecar append; unparsed sees it."""
    env = user.env | {"PYTHONPATH": str(PLUGIN / "src")}
    pairs = ["record", "demo", "skills", "skills=tdd"]
    crashed = subprocess.run([user.env["DL_PYTHON"], "-c", CRASH, *pairs], cwd=work, env=env,
                             capture_output=True, text=True, timeout=TIMEOUT, check=False)
    assert crashed.returncode == 9, crashed
    assert sidecar(work) == []  # propose made it empty (v0.4.20 spec 3); the append never ran
    gap = user.dl(work, "ci", "unparsed")
    assert gap.returncode == 1 and "no record: Skills used: tdd" in gap.stdout, gap.stdout


def repaired(user: Run, work: Path) -> None:
    done = record(user, work, "skills", "skills=tdd")
    assert done.returncode == 0, done.stderr
    assert len(sidecar(work)) == 1
    status = (changes(work) / "status.md").read_text(encoding="utf-8")
    assert status.count("Skills used: tdd") == 1, status


def test_r7_crash_then_rerun_at_once(user: Run, work: Path) -> None:
    crash_after_markdown(user, work)
    repaired(user, work)


def test_r7_crash_then_rerun_once_the_lock_is_stale(user: Run, work: Path) -> None:
    crash_after_markdown(user, work)
    lock = changes(work) / ".record.lock"
    assert lock.exists()
    old = time.time() - 120  # past LOCK_STALE: the re-run may break the dead writer's lock
    os.utime(lock, (old, old))
    repaired(user, work)


def gaps(user: Run, work: Path) -> str:
    done = user.dl(work, "record", "done")
    assert done.returncode == 0, done
    return done.stdout


def test_r8_done_rule_names_each_gap(user: Run, work: Path) -> None:
    assert "a clean last review round" in gaps(user, work)  # no rounds
    record(user, work, "round", *[p for p in ROUND if not p.startswith(("major", "fixed"))],
           "major=1", "fixed=1")
    said = gaps(user, work)
    assert "a clean last review round" in said, said  # dirty last round
    assert "'Lessons logged: n' (n >= 1 fixed" in said, said  # fixed > lessons
    record(user, work, "round", *[p for p in ROUND if not p.startswith("round=")], "round=2")
    record(user, work, "lesson", *KINDS["lesson"])
    record(user, work, "verify", "result=red", "head=HEAD")
    said = gaps(user, work)
    for gap in ("a green verify record", "a simplifier line", "a skills line",
                "a metrics row for PR 7", "'Lessons logged: n'"):
        assert gap in said, (gap, said)
    assert "a clean last review round" not in said, said
    for kind, *pairs in [["metrics", *METRICS], ["lessons-logged", "count=1"], *DONE[:3]]:
        assert record(user, work, kind, *pairs).returncode == 0, kind
    assert gaps(user, work) == ""


def test_r10_shipped_pitfalls_resolve_a_class(user: Run, work: Path) -> None:
    lesson = ["Summary: the cause", "Avoid: do it. Self-check: run it.", "Class: 2",
              "Guard: open", "Files: README.md", "Round: 1"]
    done = user.dl(work, "lesson", "demo", "high", *lesson)
    assert done.returncode == 0, done
    assert sidecar(work)[-1]["class"] == "2 Not run shown as passed", sidecar(work)[-1]
    current = user.dl(work, "ci", "records-current")
    assert current.returncode == 0, current


def test_r11_no_stale_command_name_ships() -> None:
    stale = re.compile(r"(^|[^-\w])loop (record|records|lesson)\b|scripts/record\.py"
                       r"|scripts/review_records\.py|log-lesson\.sh|review_records\.py unparsed")
    hits = [f"{path.relative_to(PLUGIN)}:{n}" for top in
            ("templates", "skills", "agents", "hooks", "src", "bin")
            for path in (PLUGIN / top).rglob("*") if path.is_file()
            and "__pycache__" not in path.parts
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if stale.search(line)]
    assert hits == [], hits


VIEWS = ["follow-ups", "metrics", "lessons", "current", "trend", "pitfalls", "rules",
         "evaluate", "unparsed", "recall", "repeat", "examples"]


def test_r12_every_view_reads_all_changes(user: Run, work: Path) -> None:
    for name in ("two", "three"):  # `dl propose` gives each its own worktree; these share one
        (changes(work).parent / name).mkdir()
    for name in ("demo", "two", "three"):
        for kind, pairs in (("follow-up", [f"pr={len(name)}", *KINDS["follow-up"][1:]]),
                            ("metrics", [f"pr={len(name)}", f"change={name}", *METRICS[2:]])):
            done = user.dl(work, "record", name, kind, *pairs)
            assert done.returncode == 0, (name, done)
    for view in VIEWS:
        done = user.dl(work, "records", view)
        assert done.returncode == 0, (view, done)
    follow_ups = user.dl(work, "records", "follow-ups").stdout.splitlines()
    assert len(follow_ups) == 3, follow_ups
    metrics = user.dl(work, "records", "metrics").stdout.splitlines()
    assert len(metrics) == 2 + 3, metrics
