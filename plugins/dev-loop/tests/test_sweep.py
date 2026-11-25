"""`dl tasks sweep`: a pid/git/query supervisor — one retry, then the coordinator; no model.

`agent_tasks.tasks`, `.work` and `.comment` are stubbed, so no host and no network.
"""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from dev_loop.tasks import agent_tasks, sweep


class FakeWork:
    """A host adapter stub: the comments a task carries and the tags the sweep adds."""

    def __init__(self, notes: dict[int, list[dict[str, Any]]] | None = None) -> None:
        self.notes = notes or {}
        self.tagged: list[tuple[int, str]] = []

    def comments(self, item_id: int) -> list[dict[str, Any]]:
        return self.notes.get(item_id, [])

    def add_tag(self, item_id: int, tag: str) -> None:
        self.tagged.append((item_id, tag))


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> tuple[list, FakeWork, list]:
    items: list[dict[str, Any]] = []
    fake = FakeWork()
    said: list[tuple[str, str]] = []
    monkeypatch.setattr(agent_tasks, "tasks", lambda: items)
    monkeypatch.setattr(agent_tasks, "work", lambda: fake)
    monkeypatch.setattr(agent_tasks, "comment", lambda i, n: said.append((i, n)))
    return items, fake, said


def _task(item_id: int, state: str, tags: str = "", created: str = "") -> dict[str, Any]:
    fields = {"System.State": state, "System.Tags": tags}
    if created:
        fields["System.CreatedDate"] = created
    return {"id": item_id, "fields": fields}


def _ago(hours: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours)).isoformat()


def _note(text: str, when: str) -> dict[str, Any]:
    return {"id": 1, "text": f"<pre>{text}</pre>", "createdDate": when}


def _repo(tmp_path: Path, *, dirty: bool, pushed: bool) -> Path:
    tree = tmp_path / "wt" / "7"
    tree.mkdir(parents=True)
    run = ["git", "-C", str(tree)]
    subprocess.run([*run, "init", "-q", "-b", "work"], check=True)
    subprocess.run([*run, "config", "user.email", "noreply@example.com"], check=True)
    subprocess.run([*run, "config", "user.name", "t"], check=True)
    (tree / "f.txt").write_text("x")
    subprocess.run([*run, "add", "."], check=True)
    subprocess.run([*run, "commit", "-qm", "one"], check=True)
    if pushed:
        bare = tmp_path / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
        subprocess.run([*run, "remote", "add", "origin", str(bare)], check=True)
        subprocess.run([*run, "push", "-q", "origin", "work"], check=True)
    if dirty:
        (tree / "f.txt").write_text("changed")
    return tmp_path / "wt"


def test_live_run_reads_the_lock_dir(tmp_path: Path) -> None:
    run = tmp_path / "run"
    (run / "7").mkdir(parents=True)
    assert not sweep.live_run(run, "7")  # no pid, no again
    (run / "7" / "pid").write_text(str(os.getpid()))
    assert sweep.live_run(run, "7")
    (run / "7" / "pid").write_text("99999999")
    assert not sweep.live_run(run, "7")
    (run / "7" / "again").touch()
    assert sweep.live_run(run, "7")  # a queued re-run counts as live


def test_worktree_unfinished_sees_dirty_and_unpushed(tmp_path: Path) -> None:
    clean = _repo(tmp_path / "a", dirty=False, pushed=True)
    assert not sweep.worktree_unfinished(clean / "7")
    dirty = _repo(tmp_path / "b", dirty=True, pushed=True)
    assert sweep.worktree_unfinished(dirty / "7")
    unpushed = _repo(tmp_path / "c", dirty=False, pushed=False)
    assert sweep.worktree_unfinished(unpushed / "7")
    assert not sweep.worktree_unfinished(tmp_path / "nowhere")


def test_a_dirty_stall_is_re_run_once_then_handed_over(wired, tmp_path, capsys) -> None:
    items, fake, said = wired
    items.append(_task(7, "Doing"))
    roots = _repo(tmp_path, dirty=True, pushed=True)
    run = tmp_path / "run"
    run.mkdir()
    sweep.sweep(str(run), str(roots), 24)
    assert capsys.readouterr().out.strip() == "#7"  # printed for the runner to re-run
    assert (run / ".retried-7").exists()
    assert not said and not fake.tagged
    sweep.sweep(str(run), str(roots), 24)  # still Doing, no live run: now hand it over
    assert capsys.readouterr().out.strip() == ""
    assert fake.tagged == [(7, "needs-coordinator")]
    assert said and "one retry" in said[0][1]


def test_a_clean_pushed_worktree_is_left_alone(wired, tmp_path, capsys) -> None:
    items, fake, said = wired
    items.append(_task(7, "Doing"))
    roots = _repo(tmp_path, dirty=False, pushed=True)
    run = tmp_path / "run"
    run.mkdir()
    sweep.sweep(str(run), str(roots), 24)
    assert capsys.readouterr().out.strip() == ""
    assert not said and not fake.tagged and not (run / ".retried-7").exists()


def test_a_stale_question_is_handed_over_a_fresh_one_waits(wired, tmp_path) -> None:
    items, fake, said = wired
    items.append(_task(7, "Doing"))
    fake.notes[7] = [_note("[worker w1] QUESTION: which base?", _ago(48))]
    run = tmp_path / "run"
    run.mkdir()
    sweep.sweep(str(run), str(tmp_path / "wt"), 24)
    assert fake.tagged == [(7, "needs-coordinator")]
    assert "question" in said[0][1]
    fake.tagged.clear()
    said.clear()
    fake.notes[7] = [_note("[worker w1] QUESTION: which base?", _ago(2))]
    sweep.sweep(str(run), str(tmp_path / "wt"), 24)
    assert not fake.tagged and not said  # a fresh question still waits for the coordinator


def test_a_long_queued_task_is_handed_over(wired, tmp_path) -> None:
    items, fake, said = wired
    items.append(_task(7, "To Do", created=_ago(48)))
    items.append(_task(8, "To Do", created=_ago(1)))
    run = tmp_path / "run"
    run.mkdir()
    sweep.sweep(str(run), str(tmp_path / "wt"), 24)
    assert fake.tagged == [(7, "needs-coordinator")]  # 8 is still fresh
    assert "queued" in said[0][1]


def test_an_already_tagged_task_is_skipped(wired, tmp_path, capsys) -> None:
    items, fake, said = wired
    items.append(_task(7, "Doing", tags="agent; needs-coordinator"))
    run = tmp_path / "run"
    run.mkdir()
    sweep.sweep(str(run), str(tmp_path / "wt"), 24)
    assert capsys.readouterr().out.strip() == ""
    assert not said and not fake.tagged  # no third retry
