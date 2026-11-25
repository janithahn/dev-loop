"""V23: on the local host a round is recorded only behind a clean `review-run` of its PR."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from test_record import LESSON, ROUND, make

from dev_loop.records import record, round_guard

PR = "c2425df1f07d"
PAIRS = [f"{k}={v}" for k, v in ROUND.items() if k != "pr"] + [f"pr={PR}"]


def local(root: Path, extra: str = "") -> Path:
    (root / "dev-loop.yaml").write_text(
        "version: 1\npaths:\n  changes: work/log\n  archive: work/log/archive\n"
        f"host:\n  provider: local\n{extra}", encoding="utf-8")
    return make(root, "x")


def ran(folder: Path, rc: int, pr: str = PR, head: str = "a" * 40) -> None:
    run = {"kind": "review-run", "pr": pr, "head": head, "base": "full", "rc": rc}
    with (folder / "host.jsonl").open("a", encoding="utf-8") as file:
        file.write(json.dumps(run) + "\n")


def test_refused_after_a_failed_review_run(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    folder = local(root)
    ran(folder, 0)
    ran(folder, 1)
    with pytest.raises(SystemExit) as stop:
        record.main(["x", "round", *PAIRS])
    assert stop.value.code == 2
    assert f"no clean review-run for {PR}" in capsys.readouterr().err
    assert not (folder / "records.jsonl").exists()


def test_refused_with_no_review_run(root: Path) -> None:
    local(root)
    with pytest.raises(SystemExit):
        record.main(["x", "round", *PAIRS])


def test_allowed_after_a_clean_review_run(root: Path) -> None:
    folder = local(root)
    ran(folder, 1)
    ran(folder, 0)
    ran(folder, 1, pr="0" * 12)  # another PR's failure does not count
    assert record.main(["x", "round", *PAIRS]) == 0


def test_allowed_with_the_subagent_fallback(root: Path) -> None:
    local(root, "review:\n  fallback: subagent\n")
    assert record.main(["x", "round", *PAIRS]) == 0


def lessons(folder: Path, count: int) -> None:
    for _ in range(count):
        record.append(folder, "x", {"kind": "lesson", **{k: v for k, v in LESSON.items()}})


def test_lessons_logged_count_must_match_the_lessons(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = local(root)
    lessons(folder, 1)
    with pytest.raises(SystemExit) as stop:
        record.main(["x", "lessons-logged", "count=6"])
    assert stop.value.code == 2
    assert "count=6 but 1 lesson records" in capsys.readouterr().err
    assert record.main(["x", "lessons-logged", "count=1"]) == 0


FIXED = [p for p in PAIRS if not p.startswith("fixed=")] + ["fixed=1"]
GIT = ["git", "-c", "user.name=t", "-c", "user.email=noreply@example.com"]
FIRST = {"src/a.py": "x = 1\n", "tests/test_a.py": "def test_a():\n    assert run() == 1\n"}


def commit(root: Path, files: dict[str, str]) -> str:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    subprocess.run([*GIT, "-C", str(root), "add", "-A", "--", *files], check=True)
    subprocess.run([*GIT, "-C", str(root), "commit", "-q", "-m", "m"], check=True)
    head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True,
                          capture_output=True, text=True)
    return head.stdout.strip()


def integration(root: Path, sha: str) -> None:
    subprocess.run(["git", "-C", str(root), "update-ref", "refs/remotes/origin/main", sha],
                   check=True)


def started(root: Path) -> tuple[Path, str]:
    """A project whose integration ref holds FIRST, and a review-run at that head."""
    folder = local(root, 'size:\n  production: ["src/**"]\n')
    base = commit(root, FIRST)
    integration(root, base)
    ran(folder, 0, head=base)
    return folder, base


def test_refused_when_the_fix_cut_an_assertion(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder, _ = started(root)
    commit(root, {"tests/test_a.py": "def test_a():\n    assert run() == 2\n"})
    with pytest.raises(SystemExit) as stop:
        record.main(["x", "round", *FIXED])
    assert stop.value.code == 2
    assert "the fix cut an assertion (assert run() == 1)" in capsys.readouterr().err
    assert not (folder / "records.jsonl").exists()
    commit(root, {"tests/test_a.py": FIRST["tests/test_a.py"] + "def test_b():\n"
                  "    assert run() == 2\n"})
    assert record.main(["x", "round", *FIXED]) == 0


def test_refused_when_a_source_fix_adds_no_test(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    started(root)
    commit(root, {"src/a.py": "x = 2\n"})
    with pytest.raises(SystemExit) as stop:
        record.main(["x", "round", *FIXED])
    assert stop.value.code == 2
    err = capsys.readouterr().err
    assert "fixed=1: source changed since" in err and "no test was added" in err
    commit(root, {"tests/test_a.py": FIRST["tests/test_a.py"] + "def test_b():\n"
                  "    assert True\n"})
    assert record.main(["x", "round", *FIXED]) == 0


def test_a_record_only_fix_needs_no_test(root: Path) -> None:
    started(root)
    commit(root, {"NOTES.md": "n\n", "work/log/x/design.md": "d\n"})
    assert record.main(["x", "round", *FIXED]) == 0


def test_a_pr_may_correct_a_test_a_fix_round_added(root: Path) -> None:
    folder = local(root, 'size:\n  production: ["src/**"]\n')
    first = commit(root, FIRST)
    ran(folder, 0, head=first)
    added = commit(root, {"tests/test_b.py": "def test_b():\n    assert run() == 1\n"})
    ran(folder, 0, head=added)
    commit(root, {"src/a.py": "x = 2\n", "tests/test_b.py": "def test_b():\n"
                  "    assert run() == 2\n"})
    assert record.main(["x", "round", *FIXED]) == 0


def test_a_test_the_pr_had_when_first_reviewed_is_kept(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = local(root, 'size:\n  production: ["src/**"]\n')
    integration(root, commit(root, {"src/a.py": "x = 1\n"}))  # the test is not on integration
    ran(folder, 0, head=commit(root, FIRST))
    commit(root, {"tests/test_a.py": "def test_a():\n    assert run() == 2\n"})
    with pytest.raises(SystemExit) as stop:
        record.main(["x", "round", *FIXED])
    assert stop.value.code == 2
    assert "the fix cut an assertion (assert run() == 1)" in capsys.readouterr().err


def test_a_base_that_is_no_commit_is_not_passed_to_git(root: Path) -> None:
    local(root, "review:\n  fallback: subagent\n")
    pairs = [p for p in PAIRS if not p.startswith("base=")] + ["base=--output=x"]
    assert record.main(["x", "round", *pairs]) == 0
    assert not (root / "x").exists()


WORDS = "the loop settles a case the proposal does not answer"


def pause(thread: int, cites: str = WORDS) -> list[str]:
    return ["x", "pause", f"pr={PR}", f"thread={thread}", f"cites={cites}", "why=breaks it"]


def proposed(folder: Path, text: str = WORDS) -> None:
    (folder / "proposal.md").write_text(f"# P\n\n{text.replace(' ', chr(10), 3)}\n", encoding="utf-8")


def test_a_pause_must_copy_words_the_proposal_holds(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = local(root)
    proposed(folder)
    with pytest.raises(SystemExit) as stop:
        record.main(pause(1, "words that appear nowhere in any file"))
    assert stop.value.code == 2
    assert "pause: no such words in proposal.md or a governance file" in capsys.readouterr().err
    assert not (folder / "records.jsonl").exists()
    assert record.main(pause(1)) == 0
    assert "Paused: PR" in (folder / "status.md").read_text(encoding="utf-8")


def test_a_pause_may_cite_a_governance_file(root: Path) -> None:
    folder = local(root, "review:\n  governance: [rules/*.md]\n")
    (root / "rules").mkdir()
    (root / "rules" / "a.md").write_text(f"x {WORDS} y", encoding="utf-8")
    proposed(folder, "nothing relevant at all in this proposal")
    assert record.main(pause(1)) == 0


def test_a_pause_of_under_20_characters_is_refused(root: Path) -> None:
    folder = local(root)
    proposed(folder)
    with pytest.raises(SystemExit) as stop:
        record.main(pause(1, "the loop settles"))
    assert stop.value.code == 2


def test_a_local_round_with_an_unpaused_active_thread_is_refused(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = local(root)
    proposed(folder)
    ran(folder, 0)
    pairs = [p for p in PAIRS if not p.startswith("active=")] + ["active=2"]
    with pytest.raises(SystemExit) as stop:
        record.main(["x", "round", *pairs])
    assert stop.value.code == 2
    assert "active=2 but 0 paused" in capsys.readouterr().err
    assert record.main(pause(1)) == 0
    assert record.main(pause(2)) == 0
    assert record.main(["x", "round", *pairs]) == 0


def test_a_hosted_round_may_leave_a_thread_active(root: Path) -> None:
    (root / "dev-loop.yaml").write_text(
        "version: 1\npaths:\n  changes: work/log\n  archive: work/log/archive\n", encoding="utf-8")
    make(root, "x")
    pairs = [p for p in PAIRS if not p.startswith("active=")] + ["active=2"]
    assert record.main(["x", "round", *pairs]) == 0


def test_a_hosted_round_1_base_is_the_review_base_not_a_fix_anchor(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """v0.4.20 D: no review-run, round 1's base is what the review diffed against: no fix check."""
    (root / "dev-loop.yaml").write_text(
        "version: 1\npaths:\n  changes: work/log\n  archive: work/log/archive\n"
        'size:\n  production: ["src/**"]\n', encoding="utf-8")
    make(root, "x")
    base = commit(root, FIRST)
    integration(root, base)
    commit(root, {"tests/test_a.py": "def test_a():\n    assert run() == 2\n"})
    pairs = [p for p in FIXED if not p.startswith("base=")] + [f"base={base}"]
    assert record.main(["x", "round", *pairs]) == 0
    with pytest.raises(SystemExit) as stop:
        record.main(["x", "round", *[p for p in pairs if p != "round=1"], "round=2"])
    assert stop.value.code == 2
    assert "the fix cut an assertion (assert run() == 1)" in capsys.readouterr().err


def test_a_hosted_round_1_source_fix_still_needs_a_test(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Review major 5: round 1 with no review-run skips only the cut-assertion check."""
    (root / "dev-loop.yaml").write_text(
        "version: 1\npaths:\n  changes: work/log\n  archive: work/log/archive\n"
        'size:\n  production: ["src/**"]\n', encoding="utf-8")
    make(root, "x")
    base = commit(root, FIRST)
    commit(root, {"src/a.py": "x = 2\n"})
    pairs = [p for p in FIXED if not p.startswith("base=")] + [f"base={base}"]
    with pytest.raises(SystemExit) as stop:
        record.main(["x", "round", *pairs])
    assert stop.value.code == 2
    assert "no test was added" in capsys.readouterr().err


def test_fast_track_sees_a_moved_integration_branch(
    root: Path, tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """The cached value follows origin/<integration>, not only the config file's mtime."""
    config = "version: 1\nbranches: {integration: trunk-x}\n"
    commit(root, {"dev-loop.yaml": config})
    bare, clone = (tmp_path_factory.mktemp(n) / "r" for n in ("bare", "clone"))
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    subprocess.run(["git", "-C", str(root), "remote", "add", "origin", str(bare)], check=True)
    subprocess.run(["git", "-C", str(root), "push", "-q", "origin", "HEAD:trunk-x"], check=True)
    subprocess.run(["git", "-C", str(root), "fetch", "-q", "origin"], check=True)
    assert round_guard.fast_track(root) is False
    subprocess.run(["git", "clone", "-q", "-b", "trunk-x", str(bare), str(clone)], check=True)
    commit(clone, {"dev-loop.yaml": config + "review: {fast_track: true}\n"})
    subprocess.run(["git", "-C", str(clone), "push", "-q", "origin", "trunk-x"], check=True)
    subprocess.run(["git", "-C", str(root), "fetch", "-q", "origin"], check=True)
    assert (root / "dev-loop.yaml").read_text(encoding="utf-8") == config  # untouched
    assert round_guard.fast_track(root) is True
