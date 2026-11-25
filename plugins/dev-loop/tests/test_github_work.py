"""`dl tasks` on GitHub Issues through the fake `gh` of test_host_github."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_host_github import _log, _replies, gh  # noqa: F401 - gh is the fixture
from test_neutral import check

from dev_loop.hosts import HostError, github_work
from dev_loop.tasks import agent_tasks

TAG = "board-tag"
BOSS = "boss"


def _issue(number: int, labels: list[str], state: str = "open", **extra: object) -> dict:
    return {"number": number, "title": "T", "body": "<pre>a &amp; b</pre>", "state": state,
            "user": {"login": BOSS}, "labels": [{"name": name} for name in labels], **extra}


NOTES = [
    {"id": 1, "user": {"login": BOSS}, "body": "<pre>do it</pre>",
     "created_at": "2026-01-01T10:00:00Z"},
    {"id": 3, "user": {"login": "other"}, "body": "<pre>ignore me</pre>",
     "created_at": "2026-01-01T10:02:00Z"},
    {"id": 2, "user": {"login": BOSS}, "body": "<pre>[worker w1] hi</pre>",
     "created_at": "2026-01-01T10:01:00Z"},
]


@pytest.fixture
def board(gh: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:  # noqa: F811
    (tmp_path / "proj" / "dev-loop.yaml").write_text(
        "version: 1\nhost: {provider: github, org: octo, repo: widgets}\n"
        f"tasks: {{tag: {TAG}, cache: '{(tmp_path / 'seen-{repo}.json').as_posix()}'}}\n"
        f"managers: ['{BOSS.upper()}']\n", encoding="utf-8")
    monkeypatch.delenv("DEV_LOOP_WORKER", raising=False)
    _replies(gh, [
        ["issues?state=open", json.dumps([
            _issue(5, [TAG]), _issue(6, [TAG], pull_request={}),
            {**_issue(7, [TAG]), "user": {"login": "other"}}]), 0, ""],
        ["issues/5/comments?per_page=100 --method GET", json.dumps(NOTES), 0, ""],
        ["issues/5 --method GET", json.dumps(_issue(5, [TAG, "doing"])), 0, ""],
        ["user --method GET", json.dumps({"login": "me-login"}), 0, ""],
        ["issues --method POST", '{"number": 9}', 0, ""],
    ])
    return gh


def test_watch_lists_manager_tasks_and_comments_only(board: Path, tmp_path: Path,
                                                     capsys) -> None:
    assert agent_tasks.main(["watch", "--once"]) == 0
    out = capsys.readouterr().out
    assert "NEW TASK #5: T" in out
    assert "#6" not in out  # a pull request is not a task
    assert "#7" not in out  # not a manager's issue
    assert "COORDINATOR COMMENT on #5: do it" in out
    assert "ignore me" not in out
    assert "hi" not in out
    assert json.loads((tmp_path / "seen-widgets.json").read_text()) == {"5": 1}
    assert not (tmp_path / "seen-widgets.json.tmp").exists()
    listed = next(c["argv"][1] for c in _log(board) if "issues?state=open" in c["argv"][1])
    assert f"labels={TAG}&" in listed
    assert "assignee=me-login" in listed


def test_a_worker_lists_by_its_label(board: Path, monkeypatch) -> None:
    monkeypatch.setenv("DEV_LOOP_WORKER", "w1")
    agent_tasks.main(["watch", "--once"])
    listed = next(c["argv"][1] for c in _log(board) if "issues?state=open" in c["argv"][1])
    assert f"labels={TAG}%2Cworker-w1&" in listed
    assert "assignee" not in listed


def test_show_prints_state_brief_and_manager_comments(board: Path, capsys) -> None:
    assert agent_tasks.main(["show", "5"]) == 0
    out = capsys.readouterr().out
    assert "#5 [Doing] T" in out
    assert "a & b" in out
    assert "do it" in out
    assert "ignore me" not in out


def test_comment_is_an_issue_comment_marked_by_the_worker(board: Path, monkeypatch) -> None:
    monkeypatch.setenv("DEV_LOOP_WORKER", "w1")
    agent_tasks.main(["comment", "5", "a <b>"])
    call = _log(board)[-1]
    assert call["argv"][1:4] == ["repos/octo/widgets/issues/5/comments", "--method", "POST"]
    assert json.loads(call["stdin"]) == {"body": "<pre>[worker w1] a &lt;b&gt;</pre>"}


def test_start_labels_doing_and_done_closes(board: Path) -> None:
    agent_tasks.main(["start", "5"])
    agent_tasks.main(["done", "5", "PR 1"])
    calls = [(c["argv"][1], c["argv"][3], c["stdin"]) for c in _log(board)]
    issue = "repos/octo/widgets/issues/5"
    assert (issue + "/labels", "POST", json.dumps({"labels": ["doing"]})) in calls
    assert calls.index((issue, "PATCH", json.dumps({"state": "closed"}))) > 0
    assert calls[-1][0] == issue + "/comments"


def test_back_to_todo_removes_doing(board: Path) -> None:
    github_work.set_state(5, "To Do")
    calls = [(c["argv"][1], c["argv"][3]) for c in _log(board)]
    assert ("repos/octo/widgets/issues/5", "PATCH") in calls
    assert ("repos/octo/widgets/issues/5/labels/doing", "DELETE") in calls
    with pytest.raises(HostError, match="state"):
        github_work.set_state(5, "Parked")


def test_new_opens_a_labelled_issue(board: Path, tmp_path: Path, capsys) -> None:
    brief = tmp_path / "brief.md"
    brief.write_text("do <x>")
    assert agent_tasks.main(["new", "w2", "Title", str(brief)]) == 0
    sent = json.loads(_log(board)[-1]["stdin"])
    assert sent == {"title": "Title", "body": "<pre>do &lt;x&gt;</pre>",
                    "labels": [TAG, "worker-w2"]}
    assert "#9 for w2" in capsys.readouterr().out


def test_log_shows_every_comment_oldest_first(board: Path, capsys) -> None:
    agent_tasks.main(["log", "5"])
    out = capsys.readouterr().out
    assert out.index("do it") < out.index("[worker w1] hi") < out.index("ignore me")


def test_files_are_neutral() -> None:
    root = Path(__file__).resolve().parents[1]
    assert check([root / "src/dev_loop/hosts/github_work.py", Path(__file__)]) == []
