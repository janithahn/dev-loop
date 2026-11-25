"""`agent_tasks.reopen_closed`: a coordinator comment on a Done task reopens it to re-run.

`work`, `instructions` and `comment` are stubbed, so no host and no network.
"""

from __future__ import annotations

from typing import Any

import pytest

from dev_loop.tasks import agent_tasks


class FakeWork:
    def __init__(self, state: str) -> None:
        self.state = state
        self.moved: list[tuple[int, str]] = []

    def get_item(self, item_id: int) -> dict[str, Any]:
        return {"id": item_id, "fields": {"System.State": self.state}}

    def set_state(self, item_id: int, state: str) -> None:
        self.moved.append((item_id, state))
        self.state = state


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch):
    def make(state: str, notes: list[dict[str, Any]]):
        fake = FakeWork(state)
        monkeypatch.setattr(agent_tasks, "work", lambda: fake)
        monkeypatch.setattr(agent_tasks, "instructions", lambda key: notes)
        monkeypatch.setattr(agent_tasks, "comment", lambda i, n: None)
        return fake
    return make


def test_a_newer_coordinator_comment_reopens_a_done_task(wired, capsys) -> None:
    fake = wired("Done", [{"id": 3}])
    seen = {"5": 2}
    agent_tasks.reopen_closed(seen, set())
    assert fake.moved == [(5, "Doing")]
    assert seen["5"] == 3
    assert "REOPENED #5" in capsys.readouterr().out


def test_no_new_comment_leaves_a_done_task_closed(wired, capsys) -> None:
    fake = wired("Done", [{"id": 2}])  # same id the watch already announced
    seen = {"5": 2}
    agent_tasks.reopen_closed(seen, set())
    assert fake.moved == []
    assert "REOPENED" not in capsys.readouterr().out


def test_an_open_task_is_not_reopened(wired, capsys) -> None:
    fake = wired("Doing", [{"id": 9}])
    seen = {"5": 2}
    agent_tasks.reopen_closed(seen, {"5"})  # still in the open set
    assert fake.moved == []
    assert "REOPENED" not in capsys.readouterr().out
