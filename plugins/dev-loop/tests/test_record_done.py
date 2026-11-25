"""The done rule's checks for record.py (`record.missing`), split from test_record.py (that
file passed the 400-line hard stop). Run by test_record.main.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from dev_loop import status
from dev_loop import status
from dev_loop.records import record, review_records, round_guard
from test_record import DONE, LESSON, METRICS, ROUND, finished, make, migrate, put


def test_done_rule(root: Path) -> None:
    """Each missing line of the done rule is named; history and open work are not judged."""
    gaps = {
        "a green verify": ("verify", {"result": "red", "head": "HEAD"}),
        "a simplifier line": None,
        "a skills line": None,
        "'Lessons logged: n' (n >= 2 fixed": ("lessons-logged", {"count": "1"}),
        "a metrics row for PR 8": ("round", ROUND | {"pr": "8"}),
    }
    for number, (gap, extra) in enumerate(gaps.items()):
        name = f"c{number}"
        folder = make(root, name)
        put(root, name, "round", ROUND | {"major": "2", "fixed": "2"})
        put(root, name, "round", ROUND | {"round": "2"})
        for n in range(2):  # two findings, two lessons (the same lesson twice is written once)
            put(root, name, "lesson", LESSON | {"round": str(n + 1)})
        put(root, name, "metrics", METRICS)
        for kind, fields in [*DONE[:-1], ("lessons-logged", {"count": "2"})]:
            if gap != f"a {kind} line":
                put(root, name, kind, fields)
        if extra:
            put(root, name, *extra)
        assert [g for g in record.missing(folder) if g.startswith(gap)], (gap, record.missing(folder))
        assert len(record.missing(folder)) == 1, record.missing(folder)
    folder = make(root, "short")  # two lessons logged, one written
    finished(root, "short")
    put(root, "short", "lessons-logged", {"count": "1"})
    assert record.missing(folder) == ["'Lessons logged: n' (n >= 0 fixed, each a lesson)"]
    folder = make(root, "no-report")  # the report is the shared gate
    finished(root, "no-report")
    (folder / "verify-report.md").unlink()
    assert record.missing(folder) == ["a green verify record and verify-report.md"]
    history, forged = make(root, "history"), make(root, "forged")
    for folder in (history, forged):
        (folder / "records.jsonl").write_text("", encoding="utf-8")
    migrate(root, "history", "going-on", "loop-ended", "still-open", "never-reviewed", "unstated")
    assert record.missing(history) == []  # migrated, nothing to judge
    assert record.missing(forged) != []  # an empty sidecar outside the migration: judged
    check_mixed(root)
    assert record.missing(make(root, "new")) != []  # no sidecar yet: everything is missing
    assert not record.tasks_done(make(root, "empty", "no boxes\n"))


def check_mixed(root: Path) -> None:
    """A migrated change going on is judged on its new rounds, never a legacy null."""
    folder = make(root, "going-on")
    old = {"kind": "round", "line": "Review round 1 (base x): 0 blocker, 2 major", "legacy": True}
    old |= dict.fromkeys(("pr", "round", "base", "minor", "fixed", "deferred", "active"))
    old |= {"round": 1, "blocker": 0, "major": 2}
    (folder / "records.jsonl").write_text(json.dumps(old) + "\n", encoding="utf-8")
    finished(root, "going-on")
    assert record.missing(folder) == [], record.missing(folder)  # no "a metrics row for PR None"
    put(root, "going-on", "round", ROUND | {"round": "2", "major": "1", "fixed": "1"})
    assert record.missing(folder) == [
        "a clean last review round",
        "'Lessons logged: n' (n >= 1 fixed, each a lesson)",
    ], record.missing(folder)
    folder = make(root, "loop-ended")  # its loop ended clean under the old rules
    clean = old | {"line": "Review round 2 (base y): 0 blocker, 0 major", "major": 0}
    hand = {"kind": "verify", "result": "green", "head": "abc", "line": "Verify: x", "legacy": True}
    (folder / "records.jsonl").write_text(json.dumps(clean) + "\n" + json.dumps(hand) + "\n", "utf-8")
    (folder / "status.md").write_text(f"{clean['line']}\n\n{hand['line']}\n", encoding="utf-8")
    put(root, "loop-ended", "simplifier", {"edits": "none"})
    put(root, "loop-ended", "skills", {"skills": "tdd"})
    put(root, "loop-ended", "lessons-logged", {"count": "0"})
    assert record.missing(folder) == ["a green verify record and verify-report.md"]  # 9143
    folder = make(root, "still-open")  # its last legacy round filed a major
    dirty = old | {"line": "Review round 3 (base z): 0 blocker, 1 major", "major": 1}
    (folder / "status.md").write_text(dirty["line"] + "\n", encoding="utf-8")
    (folder / "records.jsonl").write_text(json.dumps(dirty) + "\n", encoding="utf-8")
    for kind, fields in DONE:
        put(root, "still-open", kind, fields)
    assert record.missing(folder) == ["a clean last review round"], record.missing(folder)
    unstated = old | {"line": "Review round 1: —", "blocker": None, "major": None}
    for name, legacy in (("never-reviewed", None), ("unstated", unstated)):  # 9152
        folder = make(root, name)
        lines = [legacy] if legacy else []
        (folder / "status.md").write_text("".join(f"{r['line']}\n" for r in lines), "utf-8")
        (folder / "records.jsonl").write_text("".join(json.dumps(r) + "\n" for r in lines), "utf-8")
        for kind, fields in DONE:
            put(root, name, kind, fields)
        assert record.missing(folder) == ["a clean last review round"], (name, record.missing(folder))


def test_done_scope(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """9312: `dl record done <name>` reports only the named changes; no name, every change."""
    for name in ("one", "two"):
        make(root, name)  # tasks ticked, no records: finished but missing its lines
    assert record.main(["done", "one"]) == 0
    scoped = capsys.readouterr().out
    assert "[done?] one:" in scoped and "[done?] two:" not in scoped, scoped
    assert record.main(["done"]) == 0
    said = capsys.readouterr().out
    assert all(f"[done?] {name}:" in said for name in ("one", "two")), said


def test_done_on_an_unknown_change_is_refused(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """v0.4.22 review minor 3: a typo'd name filtered every folder out and read as clean."""
    make(root, "one")
    for argv in (["nosuch", "done"], ["done", "nosuch"], ["done", "one", "nosuch", "part=a"]):
        assert record.main(argv) == 2, argv
        said = capsys.readouterr()
        assert "dl record: no change named nosuch" in said.err and said.out == "", argv


def touched(paths: list[str], monkeypatch: pytest.MonkeyPatch) -> int:
    """Run `dl record done --touched-paths -` on `paths`, NUL-separated as git's `-z` prints."""
    data = "".join(f"{p}\0" for p in paths).encode()
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(data)))
    return record.main(["done", "--touched-paths", "-"])


def test_done_touched_keeps_change_folders(
    root: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """v422h, v422i (rewritten from the removed `--touched <names>`): only change folders count;
    none touched, or no path at all, is silent; a name is never parsed as a flag."""
    make(root, "one")  # tasks ticked, no records: finished but missing its lines
    (record.changes_dir(root) / "archive").mkdir()
    for paths in ([], ["work/log/archive/x/tasks.md", "work/log/nosuch/tasks.md", "a.md"]):
        assert touched(paths, monkeypatch) == 0
        assert capsys.readouterr() == ("", ""), paths
    assert touched(["work/log/archive/x/t.md", "work/log/one/tasks.md"], monkeypatch) == 0
    said = capsys.readouterr()
    assert "[done?] one: all tasks complete but missing" in said.out and said.err == "", said
    for argv in (["done", "nosuch"], ["done", "--touched", "one"]):
        assert record.main(argv) == 2, argv
        assert "dl record: no change named" in capsys.readouterr().err


@pytest.mark.parametrize("spelt", ["work/log", "./work/log", "work/log/", "absolute"])
def test_done_touched_paths_map_to_change_folders(
    root: Path, spelt: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """v422i: git's paths are resolved in Python, whatever the spelling of paths.changes."""
    changes = str(root / "work/log") if spelt == "absolute" else spelt
    (root / "dev-loop.yaml").write_text(
        f"version: 1\npaths:\n  changes: {changes}\n  archive: work/log/archive\n", "utf-8")
    make(root, "y")
    make(root, "archive/x")  # archived and finished: never a change
    paths = ["work/log/y/tasks.md", "work/log/archive/x/tasks.md", "work/log/migrated.txt"]
    assert touched(paths, monkeypatch) == 0
    said = capsys.readouterr()
    assert "[done?] y:" in said.out and "x:" not in said.out and said.err == "", said
    assert touched(paths[1:], monkeypatch) == 0
    assert capsys.readouterr() == ("", "")


def test_done_touched_paths_under_a_non_ascii_root(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """v422j: git's top level is never decoded in the locale's encoding (cp1252 on Windows)."""
    root = tmp_path / "proj-é"
    root.mkdir()
    (root / "dev-loop.yaml").write_text(
        "version: 1\npaths:\n  changes: work/log\n  archive: work/log/archive\n", "utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    make(root, "y")
    run = subprocess.run

    def cp1252(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        if kwargs.pop("text", False):  # what Windows Python's text mode decodes a pipe with
            kwargs["encoding"] = "cp1252"
        return run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", cp1252)
    assert touched(["work/log/y/tasks.md"], monkeypatch) == 0
    said = capsys.readouterr()
    assert "[done?] y:" in said.out, said


@pytest.mark.parametrize("kind", ["missing", "file"])
def test_done_touched_paths_under_a_bad_root_never_reads_clean(
    tmp_path: Path, kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """v422k: a root that is no folder fails in git's own call, never "nothing touched" (0)."""
    root = tmp_path / "proj"
    if kind == "file":
        root.write_text("", "utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    try:
        code = touched(["work/log/y/tasks.md"], monkeypatch)
    except SystemExit as stop:
        code = stop.code
    except (OSError, subprocess.CalledProcessError, ValueError):
        return
    assert code not in (0, None), kind


def test_unresolvable_head_is_stale(root: Path) -> None:
    """A verify whose head git cannot resolve is stale: not run is never clear."""
    make(root, "x")
    finished(root, "x")
    assert record.missing(record.changes_dir(root) / "x") == []
    put(root, "x", "verify", {"result": "green", "head": "no-such-head"})
    assert "a green verify record and verify-report.md" in record.missing(
        record.changes_dir(root) / "x")


def parts_tasks(b: str = "done") -> str:
    return ("## Part a\n\n### T1: one\n- Status: done\n\n"
            f"## Part b\n\n### T2: two\n- Status: {b}\n")


def tail(root: Path, change: str, pr: str = "7") -> None:
    put(root, change, "metrics", METRICS | {"pr": pr})


def test_a_paused_threads_fix_on_the_next_round_is_clean(root: Path) -> None:
    """24s: round 2 fixes what round 1 left active; it files nothing of its own."""
    folder = make(root, "carried")
    put(root, "carried", "round", ROUND | {"major": "1", "active": "1"})
    put(root, "carried", "lesson", LESSON)
    put(root, "carried", "round", ROUND | {"round": "2", "fixed": "1"})
    tail(root, "carried")
    for kind, fields in [*DONE[:-1], ("lessons-logged", {"count": "1"})]:
        put(root, "carried", kind, fields)
    assert record.missing(folder) == [], record.missing(folder)


def test_a_fix_nothing_carried_is_not_clean(root: Path) -> None:
    one = make(root, "minor-fix")
    put(root, "minor-fix", "round", ROUND | {"minor": "1", "fixed": "1"})
    assert "a clean last review round" in record.missing(one)
    two = make(root, "over-fix")
    put(root, "over-fix", "round", ROUND | {"active": "1"})
    put(root, "over-fix", "round", ROUND | {"round": "2", "fixed": "2"})
    assert "a clean last review round" in record.missing(two)


def test_each_started_part_needs_a_reviewed_pr(root: Path) -> None:
    folder = make(root, "parts", parts_tasks())
    finished(root, "parts")
    short = "a reviewed PR for each started part (2 started, 1 reviewed)"
    assert short in record.missing(folder), record.missing(folder)
    assert not status.line(folder).endswith("done"), status.line(folder)
    put(root, "parts", "round", ROUND | {"pr": "8"})
    tail(root, "parts", "8")
    assert record.missing(folder) == [], record.missing(folder)
    one = make(root, "part-b-todo", parts_tasks("todo"))
    finished(root, "part-b-todo")
    assert record.missing(one) == [], record.missing(one)


def test_a_dirty_round_on_an_earlier_parts_pr_is_named(root: Path) -> None:
    folder = make(root, "dirty-a", parts_tasks())
    put(root, "dirty-a", "round", ROUND | {"major": "1", "active": "1"})
    put(root, "dirty-a", "round", ROUND | {"pr": "8"})
    assert "a clean last review round" in record.missing(folder), record.missing(folder)


def test_a_part_is_done_on_its_own_tasks(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """v0.4.17 F: part a is judged on its own tasks while part b is half built."""
    text = parts_tasks("doing")
    folder = make(root, "half", text)
    finished(root, "half")
    put(root, "half", "round", ROUND | {"pr": "8"})
    tail(root, "half", "8")
    assert not record.tasks_done(folder)  # the whole change: part b is started, not done
    assert record.tasks_done(folder, "a") and not record.tasks_done(folder, "b")
    assert record.missing(folder, "a") == [], record.missing(folder, "a")
    assert record.main(["done", "part=a", "half"]) == 0
    assert capsys.readouterr().out == ""  # nothing missing for part a
    record.main(["done", "half"])
    assert capsys.readouterr().out == ""  # whole change: not all tasks done, not judged


def test_done_takes_the_change_first_too(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """v0.4.22: `dl record <change> done part=a`, the order every other kind takes."""
    folder = make(root, "half", parts_tasks("doing"))
    finished(root, "half")
    put(root, "half", "round", ROUND | {"pr": "8"})
    tail(root, "half", "8")
    before = (folder / "records.jsonl").read_bytes()
    assert record.main(["half", "done", "part=a"]) == 0
    assert capsys.readouterr().out == ""
    assert (folder / "records.jsonl").read_bytes() == before
    make(root, "gap", parts_tasks("doing"))
    put(root, "gap", "round", ROUND)
    put(root, "gap", "metrics", METRICS)
    for kind, fields in DONE:
        if kind != "skills":
            put(root, "gap", kind, fields)
    assert record.main(["gap", "done", "part=a"]) == 0
    said = capsys.readouterr().out
    assert "a skills line" in said
    assert record.main(["done", "gap", "part=a"]) == 0
    assert capsys.readouterr().out == said


def test_the_done_skill_names_the_change() -> None:
    skill = Path(__file__).resolve().parents[1] / "skills/done/SKILL.md"
    assert "dl record done <change> part=<a>" in skill.read_text(encoding="utf-8")


DEBT = {"pr": "7", "thread": "71", "file": "a.py", "severity": "major", "finding": "f", "why": "w"}


def fast(root: Path, track: str, debt: bool = True, active: str = "0") -> Path:
    """v0.4.20 D: one round, 2 majors, 1 fixed; every other done line present."""
    with (root / "dev-loop.yaml").open("a", encoding="utf-8") as config:
        config.write(f"review:\n  fast_track: {track}\n")
    folder = make(root, "x")
    finished(root, "x", ROUND | {"major": "2", "fixed": "1", "active": active})
    put(root, "x", "lesson", LESSON)
    put(root, "x", "lessons-logged", {"count": "1"})
    if debt:
        put(root, "x", "debt", DEBT)
    return folder


def test_a_fast_track_round_is_clean_with_its_rest_as_debt(root: Path) -> None:
    assert record.missing(fast(root, "true")) == []


@pytest.mark.parametrize(
    ("track", "debt", "active"), [("true", False, "0"), ("false", True, "0"), ("true", True, "1")])
def test_a_fast_track_round_is_dirty_short_of_debt_or_off(
    root: Path, track: str, debt: bool, active: str,  # noqa: FBT001
) -> None:
    folder = fast(root, track, debt, active)
    assert record.missing(folder) == ["a clean last review round"], record.missing(folder)


def test_a_minor_or_repeated_debt_line_offsets_no_major(root: Path) -> None:
    """Review minor 4: debt offsets a blocker or major of its own severity, once per thread."""
    folder = fast(root, "true", debt=False)
    put(root, "x", "debt", DEBT | {"severity": "minor"})
    assert record.missing(folder) == ["a clean last review round"], record.missing(folder)
    put(root, "x", "debt", DEBT | {"thread": "72", "finding": "g"})
    put(root, "x", "debt", DEBT | {"thread": "72", "finding": "h"})
    assert record.missing(folder) == [], record.missing(folder)  # one major owed, one covered
    put(root, "x", "round", ROUND | {"pr": "7", "major": "2", "round": "2"})
    assert record.missing(folder) == ["a clean last review round"], record.missing(folder)


def test_fast_track_is_read_from_the_integration_branch(root: Path) -> None:
    """Review minor 5: a PR that sets fast track in its own config does not relax its done rule."""
    git = ["git", "-c", "user.name=t", "-c", "user.email=noreply@example.com", "-C", str(root)]
    subprocess.run([*git, "add", "dev-loop.yaml"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "config"], check=True)
    subprocess.run([*git, "update-ref", "refs/remotes/origin/main", "HEAD"], check=True)
    folder = fast(root, "true")  # set in the working tree only
    assert record.missing(folder) == ["a clean last review round"], record.missing(folder)


def test_dl_records_fast_track_reads_the_integration_branch(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Round-2 N3: the review skill's track is the done rule's, the integration branch's."""
    git = ["git", "-c", "user.name=t", "-c", "user.email=noreply@example.com", "-C", str(root)]
    subprocess.run([*git, "add", "dev-loop.yaml"], check=True)
    for config in ("config", "fast track"):
        if config == "fast track":
            round_guard._fast_track.cache_clear()  # each `dl` run is a new process
        subprocess.run([*git, "commit", "-qam", config], check=True)
        subprocess.run([*git, "update-ref", "refs/remotes/origin/main", "HEAD"], check=True)
        with (root / "dev-loop.yaml").open("a", encoding="utf-8") as file:
            file.write("review:\n  fast_track: true\n" if config == "config" else "")
        assert review_records.main(["fast-track"]) == 0
        assert capsys.readouterr().out == ("false\n" if config == "config" else "true\n")
