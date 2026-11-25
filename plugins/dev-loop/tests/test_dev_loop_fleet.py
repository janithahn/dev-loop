"""Self-check for the fleet exporter.

Stubs every HTTP call and the `cswap list --json` subprocess, and asserts the exact OTLP JSON
body posted: names, labels (every series has `repo`), values, and no secret in any label.
Export is off unless DEV_LOOP_TELEMETRY=1. Never touches the network or the real `~/.claude`.
"""

from __future__ import annotations

import http.server
import io
import json
import os
import subprocess
import threading
import urllib.error
import urllib.request
from contextlib import redirect_stderr
from pathlib import Path
from typing import Any
from unittest import mock

from dev_loop.fleet import fleet_export, transport
from test_neutral import check

real = urllib.request.build_opener(transport.NoRedirect)  # the handler, unstubbed

NOW = 1_800_000_000.0  # 2027-01-15T08:00:00Z
TOKEN, OTLP_SECRET = "pat-secret-123", "otlp-secret-456"  # noqa: S105 - fakes
ACCOUNTS = {
    "schemaVersion": 1,
    "activeAccountNumber": 2,
    "accounts": [
        {
            "number": 1,
            "email": "noreply@alice.test",
            "usageStatus": "ok",
            "usage": {
                "fiveHour": {"pct": 8.0, "resetsAt": "2027-01-15T09:00:00+00:00"},
                "sevenDay": {"pct": 24.0, "resetsAt": "2027-01-15T07:00:00+00:00"},  # past
            },
        },
        {"number": 2, "email": "noreply@bob.test", "usageStatus": "expired", "usage": None},
        {
            "number": 3,
            "email": "noreply@carol.test",
            "usageStatus": "ok",
            "usage": {"fiveHour": {"pct": None, "resetsAt": None}, "sevenDay": None},
        },
    ],
}
COMMENTS = {  # task id -> its comments; 12 asked last, 13 was answered after its question
    "11": [{"id": 1, "text": "<pre>[worker worker-b] Started.</pre>"}],
    "12": [
        {"id": 2, "text": "<pre>[worker worker-b] QUESTION: which base?</pre>"},
        {"id": 1, "text": "<pre>[worker worker-b] Started.</pre>"},
    ],
    "13": [
        {"id": 5, "text": "<pre>use the other base</pre>"},
        {"id": 4, "text": "<pre>[worker worker-b] QUESTION: which base?</pre>"},
    ],
    "14": [],
}
STATES = {"11": "Doing", "12": "Doing", "13": "Doing", "14": "To Do"}


class Fake:
    """Records each request and answers it as ADO and the OTLP gateway would."""

    def __init__(self, *, ado_down: bool = False, otlp_down: bool = False) -> None:
        self.ado_down, self.otlp_down = ado_down, otlp_down
        self.requests: list[urllib.request.Request] = []

    def __call__(self, request: urllib.request.Request, timeout: float) -> io.BytesIO:
        assert timeout <= 60, timeout
        self.requests.append(request)
        url = request.full_url
        down = self.otlp_down if url.startswith("https://otlp.test") else self.ado_down
        if down:
            raise urllib.error.URLError("unreachable")
        return io.BytesIO(json.dumps(self.answer(url)).encode())

    @staticmethod
    def answer(url: str) -> Any:  # noqa: ANN401 - JSON
        if url.startswith("https://otlp.test"):
            return {}
        if "/wiql?" in url:
            return {"workItems": [{"id": int(key)} for key in STATES]}
        if "/comments?" in url:
            return {"comments": COMMENTS[url.split("/workItems/")[1].split("/")[0]]}
        fields = [{"fields": {"System.State": state}} for state in STATES.values()]
        return {"value": fields}

    def posted(self) -> dict[str, Any]:
        sent = [r for r in self.requests if r.full_url.startswith("https://otlp.test")]
        assert len(sent) == 1, sent
        assert isinstance(sent[0].data, bytes)
        result: dict[str, Any] = json.loads(sent[0].data)
        return result


def cswap(answer: Any, *, missing: bool = False):  # noqa: ANN201, ANN401 - a stub
    def run(command: list[str], **options: Any) -> subprocess.CompletedProcess[str]:  # noqa: ANN401
        assert command[-2:] == ["list", "--json"], command
        assert options["timeout"] <= 120 and options["check"], options
        if missing:
            raise FileNotFoundError(command[0])
        return subprocess.CompletedProcess(command, 0, json.dumps(answer), "")

    return run


def run(fake: Fake, answer: Any = ACCOUNTS, **env: str) -> tuple[int, str]:  # noqa: ANN401
    """Run main() once under the stubs; return its exit code and its stderr."""
    missing = env.pop("missing", "") == "1"
    errors = io.StringIO()
    with (
        mock.patch.object(transport.OPENER, "open", fake),
        mock.patch.object(fleet_export.subprocess, "run", cswap(answer, missing=missing)),
        mock.patch.object(fleet_export.time, "time", lambda: NOW),
        mock.patch.dict(os.environ, env),
        redirect_stderr(errors),
    ):
        code = fleet_export.main(["--host", "box-1"])
    return code, errors.getvalue()


def attrs(**labels: str) -> list[dict[str, Any]]:
    return [{"key": key, "value": {"stringValue": value}} for key, value in labels.items()]


def gauge(name: str, points: list[tuple[dict[str, str], float]]) -> dict[str, Any]:
    stamp = str(int(NOW * 1e9))
    rows = [
        {
            "attributes": attrs(repo="fleet", **labels, host="box-1"),
            "timeUnixNano": stamp,
            "asDouble": value,
        }
        for labels, value in points
    ]
    return {"name": f"dev_loop_{name}", "gauge": {"dataPoints": rows}}


WORKER = {"worker": "worker-b"}
EXPECTED_WORKER = [
    gauge("worker_slots", [(WORKER, 3.0)]),
    gauge(
        "worker_tasks",
        [
            ({**WORKER, "state": "running"}, 2.0),
            ({**WORKER, "state": "todo"}, 1.0),
            ({**WORKER, "state": "doing"}, 3.0),
        ],
    ),
    gauge("worker_questions", [(WORKER, 1.0)]),
]
EXPECTED_ACCOUNTS = [
    gauge(
        "account_active",
        [
            ({"account": "1"}, 0.0),
            ({"account": "2"}, 1.0),
            ({"account": "3"}, 0.0),
        ],
    ),
    gauge(
        "account_usage_ratio",
        [
            ({"account": "1", "window": "5h"}, 0.08),
            ({"account": "1", "window": "7d"}, 0.24),
        ],
    ),
    gauge(
        "account_reset_seconds",
        [
            ({"account": "1", "window": "5h"}, 3600.0),
            ({"account": "1", "window": "7d"}, 0.0),
        ],
    ),
]


def body(metrics: list[dict[str, Any]]) -> dict[str, Any]:
    resource = {"attributes": attrs(**{"service.name": "dev-loop", "host.name": "box-1"})}
    scope = {"scope": {"name": "dev-loop.fleet"}, "metrics": metrics}
    return {"resourceMetrics": [{"resource": resource, "scopeMetrics": [scope]}]}


def check_full_batch(env: dict[str, str]) -> None:
    fake = Fake()
    code, errors = run(fake, **env)
    assert (code, errors) == (0, ""), (code, errors)
    sent = fake.posted()
    by_name = {m["name"]: m for m in sent["resourceMetrics"][0]["scopeMetrics"][0]["metrics"]}
    expected = body(EXPECTED_WORKER + EXPECTED_ACCOUNTS)
    for metric in expected["resourceMetrics"][0]["scopeMetrics"][0]["metrics"]:
        assert by_name.pop(metric["name"]) == metric, metric["name"]
    assert not by_name, by_name
    sent["resourceMetrics"][0]["scopeMetrics"][0]["metrics"] = []
    expected["resourceMetrics"][0]["scopeMetrics"][0]["metrics"] = []
    assert sent == expected, sent
    otlp = [r for r in fake.requests if r.full_url.startswith("https://otlp.test")][0]
    assert otlp.full_url == "https://otlp.test/otlp/v1/metrics", otlp.full_url
    assert otlp.get_method() == "POST"
    assert otlp.get_header("Content-type") == "application/json"
    assert otlp.get_header("Authorization") == f"Basic {OTLP_SECRET}"
    wiql = [r for r in fake.requests if "/wiql?" in r.full_url][0]
    assert wiql.full_url.startswith("https://dev.azure.com/org-x/proj-y/_apis/wit/wiql?")
    assert b"worker-worker-b" in (wiql.data or b"")
    text = otlp.data.decode() if isinstance(otlp.data, bytes) else ""
    for secret in (TOKEN, OTLP_SECRET, "noreply", "alice", "QUESTION"):
        assert secret not in text, secret


def check_failures(env: dict[str, str]) -> None:
    fake = Fake(ado_down=True)
    code, errors = run(fake, **env)
    assert code == 1 and errors.count("\n") == 1 and "workers" in errors, errors
    names = [m["name"] for m in fake.posted()["resourceMetrics"][0]["scopeMetrics"][0]["metrics"]]
    assert names == [m["name"] for m in EXPECTED_ACCOUNTS], names  # accounts still pushed

    fake = Fake(otlp_down=True)
    code, errors = run(fake, **env)
    assert code == 1 and errors.count("\n") == 1 and "OTLP" in errors, errors
    assert len([r for r in fake.requests if "otlp.test" in r.full_url]) == 1  # never retried

    fake = Fake()
    code, errors = run(fake, **env, missing="1")
    assert code == 0 and "accounts: skipped" in errors, errors
    names = [m["name"] for m in fake.posted()["resourceMetrics"][0]["scopeMetrics"][0]["metrics"]]
    assert names == [m["name"] for m in EXPECTED_WORKER], names

    fake = Fake()
    code, errors = run(fake, {"accounts": "nope"}, **env)
    assert code == 1 and "accounts" in errors and "noreply" not in errors, errors

    fake = Fake()
    code, errors = run(fake, **{k: v for k, v in env.items() if k != "DEV_LOOP_WORKER"})
    assert code == 0 and "workers: skipped" in errors, errors

    code, errors = run(Fake(), **env | {"DEV_LOOP_WORKER": "x' OR 1=1"})
    assert code == 1 and "workers" in errors, errors  # never reaches a WIQL string


def check_no_redirect(env: dict[str, str]) -> None:
    """A 3xx is refused, never followed (the auth header would leave); http is never sent."""
    hits: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            hits.append(self.path)
            self.send_response(302)
            self.send_header("Location", "/stolen")
            self.end_headers()

        def log_message(self, *_: object) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/start"
    request = urllib.request.Request(url, headers={"Authorization": "Basic x"})
    try:
        real.open(request, timeout=10)
        raise AssertionError("a redirect was followed")
    except urllib.error.HTTPError as error:
        assert error.code == 302, error.code
    finally:
        server.shutdown()
    assert hits == ["/start"], hits  # the target never saw the Authorization header

    fake = Fake()
    code, errors = run(fake, **env | {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://otlp.test/otlp"})
    assert code == 1 and "OTLP push failed (ValueError)" in errors, errors
    assert not [r for r in fake.requests if "otlp.test" in r.full_url]  # nothing sent in clear


def environment() -> dict[str, str]:
    return {
        "DEV_LOOP_TELEMETRY": "1",
        "DEV_LOOP_WORKER": "worker-b",
        "DEV_LOOP_ORG": "org-x",
        "DEV_LOOP_PROJECT": "proj-y",
        "DEV_LOOP_HOST_TOKEN": TOKEN,
        "SLOTS": "3",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "https://otlp.test/otlp/",
        "OTEL_EXPORTER_OTLP_HEADERS": f"Authorization=Basic%20{OTLP_SECRET}",
    }


def check_off(env: dict[str, str]) -> None:
    """Without DEV_LOOP_TELEMETRY=1 nothing is read, run or sent."""
    fake = Fake()
    code, errors = run(fake, **env | {"DEV_LOOP_TELEMETRY": "0"})
    assert (code, errors, fake.requests) == (0, "", [])
    code, errors = run(fake, **{k: v for k, v in env.items() if k != "DEV_LOOP_TELEMETRY"})
    assert (code, errors, fake.requests) == (0, "", [])


def agent_line(sid: str, aid: str, kind: str, context: int, at: float) -> dict[str, Any]:
    keys = ("session_id", "agent_id", "agent_type", "context_tokens", "at")
    return dict(zip(keys, (sid, aid, kind, context, at), strict=True))


def check_subagents(env: dict[str, str], tmp_path: Path) -> None:
    """agents.jsonl → one dev_loop_subagent_context point per agent, its latest line, the
    last 30 min only; a stale line, a duplicate and a corrupt line are dropped."""
    path = tmp_path / "agents.jsonl"
    fresh, stale = NOW - 60, NOW - 40 * 60
    lines: list[Any] = [
        agent_line("s1", "a1", "dev-loop:planner", 50_000, fresh - 10),  # superseded by the next
        agent_line("s1", "a1", "dev-loop:planner", 120_000, fresh),
        agent_line("s2", "a2", "dev-loop:builder-backend", 90_000, fresh),
        agent_line("s3", "a3", "code-reviewer", 7, stale),  # older than 30 min: dropped
        "{ not json",
        "[1]",  # valid JSON but not an object: dropped, never crashes the group
    ]
    text = "\n".join(json.dumps(x) if isinstance(x, dict) else x for x in lines) + "\n"
    path.write_text(text, encoding="utf-8")
    with mock.patch.object(fleet_export, "AGENTS", path):
        fake = Fake()
        code, errors = run(fake, **env)
    assert (code, errors) == (0, "")
    metrics = fake.posted()["resourceMetrics"][0]["scopeMetrics"][0]["metrics"]
    sub = [m for m in metrics if m["name"] == "dev_loop_subagent_context"]
    a1 = {"session_id": "s1", "agent_id": "a1", "agent_type": "dev-loop:planner"}
    a2 = {"session_id": "s2", "agent_id": "a2", "agent_type": "dev-loop:builder-backend"}
    assert sub == [gauge("subagent_context", [(a1, 120_000.0), (a2, 90_000.0)])], sub


def test_fleet(tmp_path: Path) -> None:
    for task in ("12", "13"):
        (tmp_path / "run" / task).mkdir(parents=True)
    (tmp_path / "run" / "stray-file").write_text("")  # not a lock dir
    with (
        mock.patch.object(fleet_export, "RUN", tmp_path / "run"),
        mock.patch.object(fleet_export, "AGENTS", tmp_path / "nope.jsonl"),
    ):
        env = environment()
        check_off(env)
        check_full_batch(env)
        check_failures(env)
        check_no_redirect(env)
        check_subagents(env, tmp_path)


def test_files_are_neutral() -> None:
    folder = Path(fleet_export.__file__).parent
    tests = [Path(__file__), Path(__file__).with_name("test_dev_loop_delivery.py")]
    assert check([*folder.glob("*.py"), *tests]) == []
