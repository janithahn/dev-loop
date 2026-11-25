"""dl tasklist: waves from Depends, a cycle refused by id, a bad status refused, set idempotent."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "src"), str(Path(__file__).parent)]

import test_neutral  # noqa: E402
from dev_loop import tasklist  # noqa: E402

BODY = """# Tasks: x

### T1: First
- Status: todo
- Kind: backend
- Files: a.py, b.py
brief one

### T2: Second
- Status: doing
- Depends: T1

### T3: Third
- Status: todo
- Depends: T1, T2
- Files: c.ts
"""


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    (tmp_path / ".dev-loop" / "changes" / "c1").mkdir(parents=True)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "commit", "-q", "--allow-empty", "-m", "base")
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    return tmp_path


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=root,
                   check=True, capture_output=True)


def _commit(root: Path, subject: str) -> None:
    _git(root, "commit", "-q", "--allow-empty", "-m", subject)


def _write(project: Path, text: str) -> Path:
    path = project / ".dev-loop" / "changes" / "c1" / "tasks.md"
    path.write_text(text, encoding="utf-8")
    return path


def test_parse_waves(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(project, BODY)
    assert tasklist.main(["parse", "c1"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [(r["id"], r["wave"]) for r in rows] == [("T1", 1), ("T2", 2), ("T3", 3)]
    assert rows[0] == {"id": "T1", "title": "First", "status": "todo", "kind": "backend",
                       "depends": [], "files": ["a.py", "b.py"], "wave": 1}
    assert rows[2]["depends"] == ["T1", "T2"] and rows[2]["kind"] is None


def _spec(project: Path, bullets: str) -> None:
    (project / ".dev-loop" / "changes" / "c1" / "spec.md").write_text(
        f"# Spec\n\n## Acceptance criteria\n{bullets}", encoding="utf-8")


def test_parse_spec_with_commands(project: Path) -> None:
    _write(project, BODY)
    _spec(project, "- `dl x` prints ok\n- run `pytest -q`: passes\n")
    assert tasklist.main(["parse", "c1"]) == 0


def test_parse_bare_bullet_refused(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(project, BODY)
    _spec(project, "- `dl x` prints ok\n- On every error above, stdout is empty.\n")
    assert tasklist.main(["parse", "c1"]) == 2
    assert "acceptance bullet names no command in backticks: On every error" in (
        capsys.readouterr().err)


def test_parse_without_spec_ok(project: Path) -> None:
    _write(project, BODY)
    assert tasklist.main(["parse", "c1"]) == 0


def test_cycle_refused_by_id(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(project, "### T1: a\n- Status: todo\n- Depends: T2\n\n"
                    "### T2: b\n- Status: todo\n- Depends: T1\n\n### T3: c\n- Status: todo\n")
    assert tasklist.main(["parse", "c1"]) == 2
    err = capsys.readouterr().err
    assert "cycle" in err and "T1" in err and "T2" in err and "T3" not in err


def test_unknown_status_refused(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _write(project, BODY)
    assert tasklist.main(["set", "c1", "T1", "finished"]) == 2
    assert "finished" in capsys.readouterr().err
    assert path.read_text(encoding="utf-8") == BODY


def test_set_edits_one_line_and_is_idempotent(project: Path) -> None:
    path = _write(project, BODY)
    _commit(project, "T3: Third")
    assert tasklist.main(["set", "c1", "T3", "done"]) == 0
    once = path.read_text(encoding="utf-8")
    assert once == BODY.replace("- Status: todo\n- Depends: T1, T2", "- Status: done\n- Depends: T1, T2")
    assert tasklist.main(["set", "c1", "T3", "done"]) == 0
    assert path.read_text(encoding="utf-8") == once


def test_set_unknown_task(project: Path) -> None:
    _write(project, BODY)
    assert tasklist.main(["set", "c1", "T9", "done"]) == 2


def test_done_needs_one_exact_commit(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(project, BODY)
    _commit(project, "T2: Second")
    assert tasklist.main(["set", "c1", "T2", "done"]) == 0
    _commit(project, "T2: Second")
    assert tasklist.main(["set", "c1", "T2", "done"]) == 2
    assert 'T2: 2 commits, need one titled "T2: Second"' in capsys.readouterr().err


def test_done_on_a_stacked_change_counts_its_own_commits(project: Path) -> None:
    _write(project, BODY)
    _git(project, "checkout", "-q", "-b", "dev-loop/c0")
    _commit(project, "T2: Parent")
    _git(project, "checkout", "-q", "-b", "dev-loop/c1")
    _commit(project, "T2: Second")
    assert tasklist.main(["set", "c1", "T2", "done"]) == 0
    _commit(project, "T2: Second")
    assert tasklist.main(["set", "c1", "T2", "done"]) == 2


def test_done_wrong_title_refused(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(project, BODY)
    _commit(project, "T2: Other words")
    assert tasklist.main(["set", "c1", "T2", "done"]) == 2
    assert "need one titled" in capsys.readouterr().err


def test_done_git_failure_exits_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    (tmp_path / ".dev-loop" / "changes" / "c1").mkdir(parents=True)
    _write(tmp_path, BODY)
    assert tasklist.main(["set", "c1", "T2", "done"]) == 2


PARTS = "## Part a\n### T1: a\n- Status: done\n### T2: b\n- Status: done\n"
PARTS += "## Part b\n### T3: c\n- Status: {}\n"


def test_a_finished_part_is_done_while_the_next_is_untouched(
        project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert tasklist.done(tasklist.parse_blocks(PARTS.format("todo")))
    assert not tasklist.done(tasklist.parse_blocks(PARTS.format("doing")))
    assert not tasklist.done(tasklist.parse_blocks(PARTS.format("todo").replace("## Part", "#")))
    _write(project, PARTS.format("todo"))
    tasklist.main(["parse", "c1"])
    by_name = capsys.readouterr().out
    tasklist.main(["parse", ".dev-loop/changes/c1"])
    assert capsys.readouterr().out == by_name


def test_done_refused_when_the_commit_touches_source_outside_files(
        project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (project / "dev-loop.yaml").write_text(
        "version: 1\nsize: {production: ['src/**']}\n", encoding="utf-8")
    task = "### T1: First\n- Status: todo\n- Files: src/a.py, tests/test_a.py\n"
    path = _write(project, task)
    for name in ("src/a.py", "src/b.py", "tests/test_a.py"):
        (project / name).parent.mkdir(exist_ok=True)
        (project / name).write_text("x\n", encoding="utf-8")
    _git(project, "add", "src", "tests")
    _git(project, "commit", "-q", "-m", "T1: First")
    assert tasklist.main(["set", "c1", "T1", "done"]) == 2
    assert "src/b.py is outside the task's Files" in capsys.readouterr().err
    assert path.read_text(encoding="utf-8") == task
    _write(project, task.replace("tests/test_a.py", "src/b.py, tests/test_a.py"))
    assert tasklist.main(["set", "c1", "T1", "done"]) == 0


def test_files_neutral() -> None:
    assert test_neutral.check(["plugins/dev-loop/src/dev_loop/tasklist.py"]) == []


def _task(tid: str, files: str | None, depends: str | None = None) -> str:
    dep = f"- Depends: {depends}\n" if depends else ""
    fil = f"- Files: {files}\n" if files else ""
    return f"### {tid}: t\n- Status: todo\n{dep}{fil}\n"


def _waves(project: Path, capsys: pytest.CaptureFixture[str], *tasks: str) -> list[int]:
    _write(project, "# Tasks: x\n\n" + "".join(tasks))
    assert tasklist.main(["parse", "c1"]) == 0
    return [r["wave"] for r in json.loads(capsys.readouterr().out)]


def test_tasks_that_share_a_file_never_share_a_wave(
        project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    waves = _waves(project, capsys, _task("T1", "a.py, pkg/__init__.py"),
                   _task("T2", "b.py, pkg/__init__.py"), _task("T3", "c.py", "T1"),
                   _task("T4", "pkg/__init__.py", "T1"))
    assert waves == [1, 2, 2, 3]


def test_tasks_with_their_own_files_still_share_a_wave(
        project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _waves(project, capsys, _task("T1", "a.py"), _task("T2", "b.py"),
                  _task("T3", None)) == [1, 1, 1]


def test_a_lettered_id_is_its_own_task(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(project, "### T4: a\n- Status: todo\n\n### T6b: b\n- Status: todo\n- Depends: T4\n\n"
                    "### T4c: c\n- Status: todo\n- Depends: T6b\n")
    assert tasklist.main(["parse", "c1"]) == 0
    tasks = json.loads(capsys.readouterr().out)
    assert [(t["id"], t["wave"], t["depends"]) for t in tasks] == [
        ("T4", 1, []), ("T6b", 2, ["T4"]), ("T4c", 3, ["T6b"])]


@pytest.mark.parametrize("head", ["T6B", "T-6", "T6b_x", "T"])
def test_any_other_id_form_is_refused_naming_the_line(
    project: Path, capsys: pytest.CaptureFixture[str], head: str,
) -> None:
    _write(project, f"### T4: a\n- Status: todo\n\n### {head}: b\n- Status: todo\n")
    assert tasklist.main(["parse", "c1"]) == 2
    assert f"### {head}: b" in capsys.readouterr().err
