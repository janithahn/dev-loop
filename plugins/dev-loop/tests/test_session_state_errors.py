"""The session-state hook sends each new batch of hook and gate errors once (0.4.18).

After the session's state record it sends one `dev_loop.gate_error` OTLP record per hook and
error class logged since the mark file (~/.claude/dev-loop/errors-sent.mark): the hook, the
class and the count, never text. A failed send moves no mark and is not itself a hook error.
The HTTP call is stubbed; every case has its own HOME.
Run: python -m pytest test_session_state_errors.py -q (from the plugin's tests folder).
"""

from __future__ import annotations

import io
import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError

import pytest
from test_dev_loop_session_state import ENV, HOOK, attribute, clean, load
from test_neutral import check

SESSION = "s0123456789abcdef"
SECRET = "PLANTED TELEMETRY SECRET"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for key in set(os.environ) - set(clean(dict(os.environ))):
        monkeypatch.delenv(key)  # the session's own telemetry settings
    for key, value in (ENV | {"HOME": str(tmp_path)}).items():
        monkeypatch.setenv(key, value)
    folder = tmp_path / ".claude" / "dev-loop"
    folder.mkdir(parents=True)
    return folder


def log(home: Path, name: str, **row: object) -> None:
    with (home / name).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")


def run(monkeypatch: pytest.MonkeyPatch, fail_on: int = 0) -> tuple[list[Any], str]:
    """Run main() on a Stop event; return the error records sent and stderr."""
    module, sent, err = load(), [], io.StringIO()

    def urlopen(request: Any, timeout: float) -> io.BytesIO:  # noqa: ANN401, ARG001
        sent.append(json.loads(request.data))
        if len(sent) == fail_on:
            raise URLError("refused")
        return io.BytesIO(b"{}")

    event = {"hook_event_name": "Stop", "session_id": SESSION, "transcript_path": "/absent"}
    monkeypatch.setattr(module, "urlopen", urlopen)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(event)))
    monkeypatch.setattr(sys, "stderr", err)
    assert module.main() == 0
    scopes = [body["resourceLogs"][0]["scopeLogs"][0] for body in sent]
    assert scopes[0]["scope"]["name"] == "dev_loop.session_state"  # the state goes first, always
    records = [record for scope in scopes[1:] for record in scope["logRecords"]]
    assert all(scope["scope"]["name"] == "dev_loop.gate_error" for scope in scopes[1:])
    return records, err.getvalue()


def expected(hook: str, error: str, count: int) -> list[dict[str, Any]]:
    return [attribute("event.name", "dev_loop.gate_error"), attribute("session.id", SESSION),
            attribute("hook", hook), attribute("error", error), attribute("count", count)]


def test_a_new_gate_error_is_one_record_sent_once(
        home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert run(monkeypatch) == ([], "")  # no log: nothing but the state
    log(home, "gate-errors.jsonl", at=time.time(), session_id=SESSION, error="SystemExit")
    log(home, "hook-errors.jsonl", at=time.time(), hook="context-gate", error="SystemExit")
    (record,), err = run(monkeypatch)
    assert (record["attributes"], err) == (expected("context-gate", "SystemExit", 1), "")
    assert record["body"] == {"stringValue": "dev_loop.gate_error"}
    assert run(monkeypatch) == ([], "")  # the batch went: not again


def test_an_error_older_than_the_mark_is_not_sent(
        home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log(home, "gate-errors.jsonl", at=time.time() - 60, error="ValueError")
    (home / "errors-sent.mark").write_text(str(time.time() - 30), "utf-8")
    assert run(monkeypatch) == ([], "")
    log(home, "hook-errors.jsonl", at=time.time(), hook="push-guard", error="OSError")
    log(home, "hook-errors.jsonl", at=time.time(), hook="push-guard", error="OSError")
    (record,), _ = run(monkeypatch)
    assert record["attributes"] == expected("push-guard", "OSError", 2)  # the new batch alone


def test_a_failed_send_is_retried_and_is_not_a_hook_error(
        home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log(home, "gate-errors.jsonl", at=time.time(), error="ValueError")
    _, err = run(monkeypatch, fail_on=2)  # the state goes, the errors' send is refused
    assert err == "session-state: not sent: URLError\n"
    assert not (home / "hook-errors.jsonl").exists()
    assert not (home / "errors-sent.mark").exists()
    (record,), err = run(monkeypatch)
    assert (record["attributes"], err) == (expected("context-gate", "ValueError", 1), "")


def test_no_text_from_a_log_line_is_sent(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log(home, "hook-errors.jsonl", at=time.time(), hook=SECRET, error=SECRET, frame=SECRET)
    (record,), _ = run(monkeypatch)
    assert record["attributes"] == expected("unknown", "unknown", 1)
    assert SECRET not in json.dumps(record)


def test_telemetry_off_sends_nothing_and_keeps_the_batch(
        home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log(home, "gate-errors.jsonl", at=time.time(), error="ValueError")
    monkeypatch.setenv("CLAUDE_CODE_ENABLE_TELEMETRY", "0")
    module = load()
    monkeypatch.setattr(module, "urlopen", lambda *_a, **_k: pytest.fail("sent"))
    assert module.main() == 0
    assert not (home / "errors-sent.mark").exists()


def test_the_files_are_neutral() -> None:
    assert check([Path(__file__), HOOK]) == []
