"""Self-check for the session-state hook (`hooks/session-state.py`).

Feeds each hook event's JSON on stdin with the HTTP call stubbed and pins the exact OTLP body;
then runs the hook as Claude Code does against a port that never answers (the 2 s budget) and one
that refuses (fail open). Fixture transcripts carry SECRET in every text field: it never reaches
the body.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from test_dev_loop_fixtures import (
    SECRET,
    SESSION,
    Record,
    Tree,
    assistant,
    tool_result,
    user,
)

if TYPE_CHECKING:
    from collections.abc import Callable


@pytest.fixture(autouse=True)
def own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The hook reads the error logs and writes its mark under HOME: never the developer's."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


HOOK = Path(__file__).resolve().parents[1] / "hooks" / "session-state.py"
ENDPOINT = "http://collector.test:4318"
ENV = {
    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
    "OTEL_LOGS_EXPORTER": "otlp",
    "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",  # as this server's settings: JSON goes too
    "OTEL_EXPORTER_OTLP_ENDPOINT": f"{ENDPOINT}/",
    "OTEL_EXPORTER_OTLP_HEADERS": "Authorization=Basic%20c2VjcmV0,X-Scope=dev",
    "OTEL_RESOURCE_ATTRIBUTES": "host.name=worker-1,dev_loop.task=162",
}
MAPPING = {
    "SessionStart": "started",
    "UserPromptSubmit": "running",
    "Stop": "waiting",
    "Notification": "waiting",
    "PreCompact": "compacting",
    "SessionEnd": "ended",
}
# What each event carries besides the common fields: every text one holds SECRET.
EXTRA: dict[str, dict[str, object]] = {
    "SessionStart": {"source": "startup"},
    "UserPromptSubmit": {"prompt": f"screen {SECRET}"},
    "Stop": {"stop_hook_active": False},
    "Notification": {"message": f"QUESTION {SECRET}", "notification_type": "idle_prompt"},
    "PreCompact": {"trigger": "manual", "custom_instructions": SECRET},
    "SessionEnd": {"reason": "prompt_input_exit"},
}


def load() -> Any:  # noqa: ANN401 - the hook's module, patched by the checks
    spec = importlib.util.spec_from_file_location("session_state", HOOK)
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def event(name: str, transcript: Path) -> dict[str, object]:
    common = {"session_id": SESSION, "transcript_path": str(transcript), "cwd": "/w"}
    return common | {"hook_event_name": name} | EXTRA[name]


def attribute(key: str, value: str | int) -> dict[str, Any]:
    typed = {"intValue": str(value)} if isinstance(value, int) else {"stringValue": value}
    return {"key": key, "value": typed}


def expected(state: str, context: int | None) -> dict[str, Any]:
    resource = [attribute("host.name", "worker-1"), attribute("dev_loop.task", "162")]
    resource.append(attribute("service.name", "claude-code"))
    attributes = [attribute("event.name", "dev_loop.session_state")]
    attributes += [attribute("session.id", SESSION), attribute("state", state)]
    attributes += [] if context is None else [attribute("context_tokens", context)]
    record = {"body": {"stringValue": "dev_loop.session_state"}, "attributes": attributes}
    scope = {"scope": {"name": "dev_loop.session_state"}, "logRecords": [record]}
    return {"resourceLogs": [{"resource": {"attributes": resource}, "scopeLogs": [scope]}]}


def clean(env: dict[str, str]) -> dict[str, str]:
    """Return the environment without the session's own telemetry settings."""
    own = ("OTEL_", "CLAUDE_CODE_ENABLE_TELEMETRY")
    return {key: value for key, value in env.items() if not key.startswith(own)}


def stubbed(
    module: Any,  # noqa: ANN401
    payload: dict[str, object] | str,
    env: dict[str, str] = ENV,
) -> tuple[Any, str, int]:
    """Run main() with stdin, env and urlopen stubbed; return the request, stderr, exit code."""
    sent: list[Any] = []

    def urlopen(request: Any, timeout: float) -> io.BytesIO:  # noqa: ANN401 - urllib's Request
        assert timeout == module.TIMEOUT == 2  # noqa: PLR2004 - the brief's budget
        sent.append(request)
        return io.BytesIO(b"{}")

    saved = sys.stdin, sys.stderr, module.urlopen, dict(os.environ)
    sys.stdin, sys.stderr, module.urlopen = (
        io.StringIO(payload if isinstance(payload, str) else json.dumps(payload)),
        io.StringIO(),
        urlopen,
    )
    os.environ.clear()
    os.environ.update(clean(saved[3]) | env)
    try:
        code = module.main()
        err = sys.stderr.getvalue()
    finally:
        sys.stdin, sys.stderr, module.urlopen = saved[:3]
        os.environ.clear()
        os.environ.update(saved[3])
    return (sent[0] if sent else None), err, code


def test_each_event_sends_one_exact_otlp_record_with_no_text(tree: Tree) -> None:
    tokens = (3, 50, 700, 9000, 120_000)  # context = 3 + 700 + 9000 + 120000
    path = tree.write([user(), assistant("m1", tool="Bash", tokens=tokens), tool_result()])
    module = load()
    for name, state in MAPPING.items():
        before = time.time_ns()
        request, err, code = stubbed(module, event(name, path))
        assert (code, err) == (0, ""), (name, err)
        assert request.full_url == f"{ENDPOINT}/v1/logs"
        assert request.get_method() == "POST"
        assert request.headers["Content-type"] == "application/json"
        assert request.headers["Authorization"] == "Basic c2VjcmV0"
        assert request.headers["X-scope"] == "dev"
        body = json.loads(request.data)
        (log,) = body["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
        assert before <= int(log.pop("timeUnixNano")) <= time.time_ns()
        assert body == expected(state, 3 + 700 + 9000 + 120_000), name
        assert SECRET not in request.data.decode()
        assert "c2VjcmV0" not in request.data.decode()  # a header is never in the body


def test_the_last_assistant_message_gives_the_context(tree: Tree) -> None:
    first = assistant("m1", tokens=(1, 1, 0, 0, 10))
    path = tree.write([first, tool_result(), assistant("m2", "end_turn", tokens=(5, 1, 6, 0, 7))])
    request, _, _ = stubbed(load(), event("Stop", path))
    body = json.loads(request.data)
    body["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0].pop("timeUnixNano")
    assert body == expected("waiting", 5 + 6 + 7)  # m2's, not m1's


def test_no_transcript_or_no_call_yet_omits_the_context(tree: Tree) -> None:
    module = load()
    for path in (tree.root / "absent.jsonl", tree.write([user()], name="fresh")):
        request, err, code = stubbed(module, event("SessionStart", path))
        body = json.loads(request.data)
        body["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0].pop("timeUnixNano")
        assert body == expected("started", None)
        assert (code, err) == (0, "")


def test_only_the_tail_is_read_and_a_cut_line_is_never_parsed(tree: Tree) -> None:
    module = load()
    path = tree.write([assistant("m1", tokens=(1, 1, 0, 0, 10)), tool_result()])
    module.TAIL = path.stat().st_size - 1  # the cut drops m1's "{", keeps its markers
    request, err, _ = stubbed(module, event("Stop", path))
    body = json.loads(request.data)
    body["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0].pop("timeUnixNano")
    assert (body, err) == (expected("waiting", None), "")  # omitted, not a parse error


def test_an_unreadable_last_call_still_sends_the_state_and_says_so(tree: Tree) -> None:
    bad = assistant("m1", tokens=(-1, 1, 0, 0, 0))  # call_of refuses a negative count
    request, err, code = stubbed(load(), event("Stop", tree.write([user(), bad])))
    body = json.loads(request.data)
    body["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0].pop("timeUnixNano")
    assert body == expected("waiting", None)
    assert (code, err) == (0, "session-state: context unread: TypeError\n")


def test_an_unknown_event_or_bad_stdin_sends_nothing_and_says_so(tree: Tree) -> None:
    module = load()
    request, err, code = stubbed(module, event("Stop", tree.root / "x") | {"hook_event_name": "Y"})
    assert (request, code) == (None, 0)
    assert err.count("\n") == 1
    assert err.startswith("session-state:")
    assert stubbed(module, "{broken") == (None, "session-state: not sent: JSONDecodeError\n", 0)


def test_telemetry_off_sends_nothing_and_says_nothing(tree: Tree) -> None:
    module = load()
    path = tree.write([user(), assistant("m1")])
    for off in (
        "CLAUDE_CODE_ENABLE_TELEMETRY",
        "OTEL_LOGS_EXPORTER",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
    ):
        env = {key: value for key, value in ENV.items() if key != off}
        assert stubbed(module, event("Stop", path), env) == (None, "", 0), off
    env = ENV | {"OTEL_LOGS_EXPORTER": "console"}
    assert stubbed(module, event("Stop", path), env) == (None, "", 0)
    env = ENV | {"CLAUDE_CODE_ENABLE_TELEMETRY": "0"}
    assert stubbed(module, {"hook_event_name": "Y"}, env) == (None, "", 0)  # parsed after


def test_the_logs_endpoint_and_headers_override_the_base_ones(tree: Tree) -> None:
    path = tree.write([user(), assistant("m1")])
    logs = {"OTEL_EXPORTER_OTLP_LOGS_ENDPOINT": "http://logs.test:4318/otlp/v1/logs"}
    logs["OTEL_EXPORTER_OTLP_LOGS_HEADERS"] = "X-Scope=logs"
    request, err, _ = stubbed(load(), event("Stop", path), ENV | logs)
    assert err == ""
    assert request.full_url == "http://logs.test:4318/otlp/v1/logs"  # used as it is, per OTel
    assert request.headers["X-scope"] == "logs"
    assert request.headers["Authorization"] == "Basic c2VjcmV0"


def test_a_grpc_protocol_sends_nothing_and_says_so(tree: Tree) -> None:
    module = load()
    path = tree.write([user(), assistant("m1")])
    for env in (
        ENV | {"OTEL_EXPORTER_OTLP_PROTOCOL": "grpc"},
        ENV | {"OTEL_EXPORTER_OTLP_LOGS_PROTOCOL": "grpc"},
    ):
        request, err, code = stubbed(module, event("Stop", path), env)
        assert (request, code) == (None, 0)
        assert err == "session-state: not sent: OTLP JSON needs an http protocol\n"
    env = ENV | {
        "OTEL_EXPORTER_OTLP_PROTOCOL": "grpc",
        "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL": "http/json",
    }
    assert stubbed(module, event("Stop", path), env)[0] is not None  # the logs one wins


def test_a_subagent_progress_record_is_not_the_sessions_call(tree: Tree) -> None:
    nested = assistant("sub", tokens=(9, 9, 9, 9, 900_000))
    progress: Record = {"type": "progress", "data": {"message": nested}, "toolUseID": "toolu_1"}
    path = tree.write([user(), assistant("m1", tokens=(1, 1, 2, 0, 3)), progress])
    request, err, _ = stubbed(load(), event("Notification", path))
    body = json.loads(request.data)
    body["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0].pop("timeUnixNano")
    assert (body, err) == (expected("waiting", 1 + 2 + 3), "")


def test_a_zero_usage_synthetic_call_keeps_the_last_count(tree: Tree) -> None:
    synthetic = assistant("s1", "stop_sequence", model="<synthetic>", tokens=(0, 0, 0, 0, 0))
    path = tree.write([user(), assistant("m1", tokens=(4, 1, 0, 0, 40)), synthetic])
    request, err, _ = stubbed(load(), event("Stop", path))
    body = json.loads(request.data)
    body["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0].pop("timeUnixNano")
    assert (body, err) == (expected("waiting", 44), "")


def hook(endpoint: str, path: Path) -> tuple[subprocess.CompletedProcess[str], float]:
    env = clean(dict(os.environ)) | ENV | {"OTEL_EXPORTER_OTLP_ENDPOINT": endpoint}
    start = time.monotonic()
    done = subprocess.run(  # noqa: S603 - our own hook
        [sys.executable, str(HOOK)],
        input=json.dumps(event("UserPromptSubmit", path)),
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=30,
    )
    return done, time.monotonic() - start


def test_a_silent_endpoint_is_cut_at_two_seconds_and_fails_open(tree: Tree) -> None:
    path = tree.write([user(), assistant("m1")])
    with socket.socket() as silent:  # accepts the connection in its backlog, never answers
        silent.bind(("127.0.0.1", 0))
        silent.listen(1)
        done, took = hook(f"http://127.0.0.1:{silent.getsockname()[1]}", path)
    assert done.returncode == 0
    assert 2 <= took < 3.5, took  # noqa: PLR2004 - the 2 s timeout plus Python's start-up
    assert done.stdout == ""
    assert done.stderr.count("\n") == 1, done.stderr
    assert done.stderr.startswith("session-state: not sent:")
    assert SECRET not in done.stderr
    assert "c2VjcmV0" not in done.stderr


def test_an_unreachable_endpoint_fails_open_with_one_line(tree: Tree) -> None:
    path = tree.write([user(), assistant("m1")])
    with socket.socket() as probe:  # a port nobody listens on: bound, then closed
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    done, took = hook(f"http://127.0.0.1:{port}", path)
    assert done.returncode == 0
    assert took < 3.5  # noqa: PLR2004
    assert done.stderr.count("\n") == 1, done.stderr
    assert done.stderr.startswith("session-state: not sent:")


def test_a_copy_outside_the_project_reads_the_context(tree: Tree) -> None:
    """The hook finds the scanner in its own plugin's source, wherever the plugin is installed."""
    copy = tree.root / "installed" / "dev-loop"
    for part in ("hooks", "src"):
        shutil.copytree(HOOK.parents[1] / part, copy / part)
    spec = importlib.util.spec_from_file_location("copied", copy / "hooks" / "session-state.py")
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    path = tree.write([assistant("m1", "end_turn", tokens=(5, 1, 6, 0, 7))])
    assert module.context_tokens(str(path)) == 5 + 6 + 7
