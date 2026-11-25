"""Only the UserPromptSubmit hook writes an approval; set doing and dl pr need one."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from dev_loop import approval, pr, tasklist
from dev_loop.records import record

HOOK = Path(__file__).resolve().parents[1] / "hooks" / "approval.py"
BODY = "### T1: First\n- Status: todo\n"


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    folder = tmp_path / ".dev-loop" / "changes" / "c1"
    folder.mkdir(parents=True)
    (folder / "tasks.md").write_text(BODY, encoding="utf-8")
    (folder / "proposal.md").write_text("# Proposal: x\n", encoding="utf-8")
    return tmp_path


def say(root: Path, prompt: str) -> subprocess.CompletedProcess[str]:
    event = {"hook_event_name": "UserPromptSubmit", "prompt": prompt, "cwd": str(root)}
    return subprocess.run(  # noqa: S603 - this interpreter and our own hook
        [sys.executable, str(HOOK)], input=json.dumps(event), capture_output=True, text=True,
        check=False, timeout=30, env=os.environ | {"CLAUDE_PROJECT_DIR": str(root)})


def records(root: Path) -> list[dict[str, str]]:
    path = root / ".dev-loop" / "changes" / "c1" / "records.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    return [json.loads(x) for x in lines]


def test_hook_writes_the_record(project: Path) -> None:
    assert say(project, "  Approved: c1 \n").returncode == 0
    (found,) = records(project)
    assert found["kind"] == "approval" and found["by"] and found["at"]
    assert record.faults(found) == []


@pytest.mark.parametrize("prompt", ["please Approved: c1", "Approved: c1 now", "approved: c1"])
def test_other_prompts_write_nothing(project: Path, prompt: str) -> None:
    say(project, prompt)
    assert records(project) == []


def test_unknown_slug_is_a_noop_with_a_line(project: Path) -> None:
    done = say(project, "Approved: nope")
    assert done.returncode == 0 and records(project) == [] and done.stderr.strip()


def test_cli_refuses_approval(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert record.main(["c1", "approval", "by=me", "at=now"]) == 1
    assert "refused" in capsys.readouterr().err and records(project) == []


def test_set_doing_needs_approval(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert tasklist.main(["set", "c1", "T1", "doing"]) == 2
    assert approval.refusal("c1") in capsys.readouterr().err
    assert tasklist.main(["set", "c1", "T1", "blocked"]) == 0  # only doing is gated
    say(project, "Approved: c1")
    assert tasklist.main(["set", "c1", "T1", "doing"]) == 0


def test_pr_refuses_before_approval(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(pr, "sh", lambda *a, **k: pytest.fail("ran git before approval"))
    assert pr.main(["c1"]) == 2
    assert approval.refusal("c1") in capsys.readouterr().err


def test_the_refusal_says_how_approval_arrives() -> None:
    assert approval.refusal("c1") == (
        'not approved: c1: approval is a user message that is exactly "Approved: c1"; in a '
        'headless run it is a turn of its own (--resume <session> "Approved: c1")')


def test_hook_marks_the_proposal_approved(project: Path) -> None:
    path = project / ".dev-loop" / "changes" / "c1" / "proposal.md"
    body = "# P\r\n**Status:** Draft\r\n| Proposal | Draft | n |\r\nDraft stays\r\n"
    path.write_bytes(body.encode())
    say(project, "Approved: c1")
    say(project, "Approved: c1")
    want = "# P\r\n**Status:** Approved\r\n| Proposal | Approved | n |\r\nDraft stays\r\n"
    assert path.read_bytes().decode() == want


def test_hook_marks_the_status_row_approved(project: Path) -> None:
    path = project / ".dev-loop" / "changes" / "c1" / "status.md"
    path.write_bytes(b"| Proposal | Draft | n |\nDraft stays\n")
    say(project, "Approved: c1")
    text = path.read_bytes().decode()
    assert text.startswith("| Proposal | Approved | n |\nDraft stays\n")
    assert text.splitlines()[-1].startswith("Approval:")


def test_hook_without_a_status_file_still_writes_the_record(project: Path) -> None:
    assert say(project, "Approved: c1").returncode == 0
    assert len(records(project)) == 1


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(  # noqa: S603, S607 - git in a temp repo
        ["git", "-c", "user.name=t", "-c", "user.email=noreply@example.com", *args],
        cwd=cwd, check=True, capture_output=True)


def _repo(tmp_path: Path) -> Path:
    main = tmp_path / "main"
    main.mkdir()
    _git("init", "-q", cwd=main)
    _git("commit", "-q", "--allow-empty", "-m", "x", cwd=main)
    return main


def _tree(main: Path, name: str, branch: str) -> Path:
    tree = main.parent / name
    _git("worktree", "add", "-q", "-b", branch, str(tree), cwd=main)
    (tree / ".dev-loop" / "changes" / "c1").mkdir(parents=True)
    return tree


def test_hook_writes_in_the_worktree_on_the_change_branch(tmp_path: Path) -> None:
    main = _repo(tmp_path)
    (main / ".dev-loop" / "changes" / "c1").mkdir(parents=True)
    wt = _tree(main, "wt", "dev-loop/c1")
    say(main, "Approved: c1")
    assert [r["kind"] for r in records(wt)] == ["approval"]
    assert not (main / ".dev-loop" / "changes" / "c1" / "records.jsonl").exists()


def test_two_stale_worktrees_write_nothing(tmp_path: Path) -> None:
    main = _repo(tmp_path)
    a, b = _tree(main, "a", "old/a"), _tree(main, "b", "old/b")
    done = say(main, "Approved: c1")
    assert done.returncode == 0
    assert records(a) == [] and records(b) == []
    assert "none is on dev-loop/c1" in done.stderr


def _recall(root: Path, shown: str) -> None:
    path = root / ".dev-loop" / "changes" / "c1" / "records.jsonl"
    with path.open("a", encoding="utf-8") as out:
        out.write(json.dumps({"kind": "recall", "change": "c1", "shown": shown}) + "\n")


def _design(root: Path, text: str) -> None:
    (root / ".dev-loop" / "changes" / "c1" / "design.md").write_text(text, encoding="utf-8")


def test_doing_runs_when_design_names_every_recalled_lesson(project: Path) -> None:
    _recall(project, "old:L1 other:L2")
    _recall(project, "old:L3 other:L15")
    say(project, "Approved: c1")
    _design(project, "- old:L3: handled by X\n- other:L15: by Y\n")
    assert approval.uncited(project / ".dev-loop" / "changes" / "c1") == []
    assert tasklist.main(["set", "c1", "T1", "doing"]) == 0


def test_doing_refused_naming_the_missing_id(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _recall(project, "old:L3 other:L15")
    say(project, "Approved: c1")
    _design(project, "- old:L3: handled by X\n")
    assert tasklist.main(["set", "c1", "T1", "doing"]) == 2
    err = capsys.readouterr().err
    assert ("design.md does not address recalled lesson(s) other:L15: name each by id with how "
            "the design handles it (plan step 3)") in err
    assert "old:L3" not in err
    assert "Status: todo" in (project / ".dev-loop" / "changes" / "c1" / "tasks.md").read_text()


def test_a_recall_after_approval_is_not_required(project: Path) -> None:
    _recall(project, "old:L3")
    say(project, "Approved: c1")
    _recall(project, "build:L9")
    _design(project, "old:L3 by X\n")
    assert tasklist.main(["set", "c1", "T1", "doing"]) == 0


def test_no_recall_no_check_and_no_design_refused(project: Path) -> None:
    say(project, "Approved: c1")
    folder = project / ".dev-loop" / "changes" / "c1"
    assert approval.uncited(folder) == []
    assert tasklist.main(["set", "c1", "T1", "doing"]) == 0
    (folder / "records.jsonl").write_text(
        '{"kind": "recall", "change": "c1", "shown": "old:L3"}\nnot json\n'
        '{"kind": "approval", "by": "u", "at": "t"}\n', encoding="utf-8")
    assert approval.uncited(folder) == ["old:L3"]
    assert tasklist.main(["set", "c1", "T1", "doing"]) == 2


@pytest.mark.parametrize(("session", "by"), [("abc-123", "user message in session abc-123"),
                                            ("../x y", "user message"), (None, "user message")])
def test_approval_names_its_session(project: Path, session: str | None, by: str) -> None:
    event = {"hook_event_name": "UserPromptSubmit", "prompt": "Approved: c1",
             "cwd": str(project), "session_id": session}
    subprocess.run(  # noqa: S603 - this interpreter and our own hook
        [sys.executable, str(HOOK)], input=json.dumps(event), capture_output=True, text=True,
        check=True, timeout=30, env=os.environ | {"CLAUDE_PROJECT_DIR": str(project)})
    (found,) = records(project)
    assert found["by"] == by and record.faults(found) == []
