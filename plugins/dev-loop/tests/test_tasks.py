"""`dl tasks` on work items: a stub host, config-driven tag, managers and cache."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from test_neutral import check

from dev_loop.tasks import agent_tasks

TAG = "board-tag"
BOSS = "boss"
TOKEN = "PLANTED-SECRET-77"  # secret-scan: ignore (a planted test value)
SEEN: list[dict] = []
NOTES = [
    {"id": 1, "createdBy": {"uniqueName": BOSS}, "text": "<pre>do it</pre>",
     "createdDate": "2026-01-01T10:00:00Z"},
    {"id": 2, "createdBy": {"uniqueName": BOSS}, "text": "<pre>[worker w1] hi</pre>",
     "createdDate": "2026-01-01T10:01:00Z"},
    {"id": 3, "createdBy": {"uniqueName": "other"}, "text": "<pre>ignore me</pre>",
     "createdDate": "2026-01-01T10:02:00Z"},
]
FIELDS = {"System.CreatedBy": {"uniqueName": BOSS}, "System.State": "To Do",
          "System.Title": "T", "System.Tags": TAG, "System.Description": "<pre>a &amp; b</pre>"}


class Stub(BaseHTTPRequestHandler):
    def _go(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode() if length else ""
        SEEN.append({"m": self.command, "p": self.path, "b": body})
        if "/wiql" in self.path:
            out = {"workItems": [{"id": 5}]}
        elif "/comments" in self.path and self.command == "GET":
            out = {"comments": NOTES}
        elif "ids=" in self.path:
            out = {"value": [{"id": 5, "fields": FIELDS}]}
        elif "$Task" in self.path:
            out = {"id": 9}
        elif self.command == "GET":
            out = {"id": 5, "fields": FIELDS}
        else:
            out = {}
        payload = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = do_PATCH = _go

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def board(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    server = HTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    SEEN.clear()
    root = tmp_path / "proj"
    root.mkdir()
    (root / "dev-loop.yaml").write_text(
        "version: 1\nhost: {org: acme, project: proj, repo: repo, token_env: DL_TEST_TOKEN}\n"
        f"tasks: {{tag: {TAG}, cache: '{(tmp_path / 'seen-{repo}.json').as_posix()}'}}\n"
        f"managers: ['{BOSS.upper()}']\n")
    git = tmp_path / "gitconfig"
    git.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(git))
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.setenv("DEV_LOOP_HOST_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("DL_TEST_TOKEN", TOKEN)
    monkeypatch.delenv("DEV_LOOP_WORKER", raising=False)
    yield tmp_path
    server.shutdown()


def test_watch_announces_manager_comments_only(board, capsys) -> None:
    assert agent_tasks.main(["watch", "--once"]) == 0
    out = capsys.readouterr().out
    assert "NEW TASK #5: T" in out
    assert "COORDINATOR COMMENT on #5: do it" in out
    assert "ignore me" not in out
    assert "hi" not in out
    assert json.loads((board / "seen-repo.json").read_text()) == {"5": 1}
    assert not (board / "seen-repo.json.tmp").exists()
    assert "System.Tags" in SEEN[0]["b"] or "wiql" in SEEN[0]["p"]
    assert TAG in SEEN[0]["b"]
    agent_tasks.main(["watch", "--once"])
    assert "NEW TASK" not in capsys.readouterr().out


def test_show_prints_brief_and_manager_comments(board, capsys) -> None:
    assert agent_tasks.main(["show", "5"]) == 0
    out = capsys.readouterr().out
    assert "a & b" in out
    assert "do it" in out
    assert "ignore me" not in out
    assert TOKEN not in out


def test_worker_comment_is_marked_and_worker_tag_queried(board, monkeypatch) -> None:
    monkeypatch.setenv("DEV_LOOP_WORKER", "w1")
    agent_tasks.main(["comment", "5", "a <b>"])
    assert json.loads(SEEN[-1]["b"])["text"] == "<pre>[worker w1] a &lt;b&gt;</pre>"
    agent_tasks.main(["watch", "--once"])
    assert "worker-w1" in next(s["b"] for s in SEEN if "/wiql" in s["p"])


def test_start_and_done_patch_state_then_comment(board) -> None:
    agent_tasks.main(["start", "5"])
    agent_tasks.main(["done", "5", "PR 1"])
    patches = [s for s in SEEN if s["m"] == "PATCH"]
    assert [json.loads(p["b"])[0]["value"] for p in patches] == ["Doing", "Done"]
    assert "preview" in SEEN[1]["p"]


def test_new_creates_tagged_task(board, capsys) -> None:
    brief = board / "brief.md"
    brief.write_text("do <x>")
    assert agent_tasks.main(["new", "w2", "Title", str(brief)]) == 0
    sent = {op["path"]: op["value"] for op in json.loads(SEEN[-1]["b"])}
    assert sent["/fields/System.Tags"] == f"{TAG}; worker-w2"
    assert sent["/fields/System.Description"] == "<pre>do &lt;x&gt;</pre>"
    assert "#9 for w2" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        agent_tasks.main(["new", "bad name'", "T", str(brief)])


def test_log_shows_every_comment(board, capsys) -> None:
    agent_tasks.main(["log", "5"])
    out = capsys.readouterr().out
    assert "ignore me" in out
    assert "[worker w1] hi" in out


def test_unreadable_cache_starts_over(board, capsys) -> None:
    (board / "seen-repo.json").write_text("{cut")
    assert agent_tasks.load_seen() == {}
    assert "unreadable" in capsys.readouterr().err


def test_host_error_exits_one_without_secret(board, monkeypatch, capsys) -> None:
    monkeypatch.setenv("DEV_LOOP_HOST_URL", "http://127.0.0.1:1")
    assert agent_tasks.main(["show", "5"]) == 1
    assert TOKEN not in capsys.readouterr().err


def test_files_are_neutral() -> None:
    root = Path(__file__).resolve().parents[1]
    files = [root / "src/dev_loop/tasks/agent_tasks.py", root / "src/dev_loop/hosts/ado_work.py",
             root / "src/dev_loop/hosts/ado.py", Path(__file__)]
    assert check(files) == []
