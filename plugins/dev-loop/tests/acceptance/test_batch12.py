"""Acceptance batch 12: telemetry and fast track.

Some rows are pinned by the existing unit tests and some are manual. Every case runs as an
isolated user (harness.Run) with each command bounded. The sink is a
local HTTPS server with a throwaway certificate (SSL_CERT_FILE trusts it), since the fleet
exporter sends over https only; it records every request's path, headers and body.
"""

from __future__ import annotations

import json
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from dev_loop.tasks import worker
from harness import PLUGIN, Run, clean_run, f_chg, f_local
from test_dev_loop_context_gate import at_context, denied, event, gate
from test_dev_loop_fixtures import SECRET, SESSION, assistant, line, user
from test_worker_runner import TAGS, _env, _read, home  # noqa: F401 - home is a fixture

if TYPE_CHECKING:
    from collections.abc import Iterator

    from test_dev_loop_fixtures import Tree

OTLP_SECRET, HOST_TOKEN = "otlp-auth-7f3e", "host-token-9c1d"
OFF = ("DEV_LOOP_", "OTEL_", "CLAUDE_CODE_", "SSL_CERT")
SESSION_STATE = PLUGIN / "hooks" / "session-state.py"


class Sink:
    """A local OTLP collector over HTTPS: every request kept as (path, headers, body)."""

    def __init__(self, base: Path) -> None:
        self.cert = base / "sink.pem"
        key = base / "sink.key"
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
             "-subj", "/CN=localhost", "-addext", "subjectAltName=IP:127.0.0.1",
             "-keyout", str(key), "-out", str(self.cert)],
            capture_output=True, timeout=60, check=True)
        self.got: list[tuple[str, str, str]] = []
        got = self.got

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                size = int(self.headers.get("Content-Length") or 0)
                got.append((self.path, str(self.headers), self.rfile.read(size).decode()))
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *_: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(self.cert, key)
        self.server.socket = tls.wrap_socket(self.server.socket, server_side=True)
        self.url = f"https://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def env(self, *, on: bool) -> dict[str, str]:
        """The collector settings a user has; `on` adds the two switches."""
        found = {"OTEL_EXPORTER_OTLP_ENDPOINT": self.url, "OTEL_LOGS_EXPORTER": "otlp",
                 "OTEL_EXPORTER_OTLP_PROTOCOL": "http/json", "SSL_CERT_FILE": str(self.cert),
                 "OTEL_EXPORTER_OTLP_HEADERS": f"Authorization=Bearer%20{OTLP_SECRET}",
                 "DEV_LOOP_HOST_TOKEN": HOST_TOKEN}
        return found | ({"DEV_LOOP_TELEMETRY": "1", "CLAUDE_CODE_ENABLE_TELEMETRY": "1"}
                        if on else {})


@pytest.fixture
def sink(tmp_path: Path) -> Iterator[Sink]:
    if not shutil.which("openssl"):
        pytest.skip("openssl is needed for the local HTTPS sink")
    found = Sink(tmp_path)
    yield found
    found.server.shutdown()


@pytest.fixture
def user_run(tmp_path: Path) -> Run:
    """harness.Run with no ambient telemetry, and no uv on PATH (cswap would reach the network)."""
    found = Run(tmp_path)
    found.env = {k: v for k, v in found.env.items() if not k.startswith(OFF)}
    found.env["PATH"] = f"{Path(sys.executable).parent}:/usr/bin:/bin"
    return found


def hook(user_run: Run, script: Path, payload: dict[str, Any], extra: dict[str, str]) -> str:
    """Run a hook as Claude Code does; return its stderr (stdout joined)."""
    done = subprocess.run(
        [sys.executable, str(script)], input=json.dumps(payload), env=user_run.env | extra,
        capture_output=True, text=True, timeout=30, check=False)
    assert done.returncode == 0, done.stderr
    return done.stdout + done.stderr


def transcript(tree: Tree) -> Path:
    """A session transcript whose prompt is the secret text and whose last call has a count."""
    path = tree.root / "session.jsonl"
    records = [user(f"read {SECRET}.pdf"), assistant("m1", tokens=(4321, 10, 0, 0, 0))]
    path.write_text("".join(line(one) + "\n" for one in records), encoding="utf-8")
    return path


def state_event(tree: Tree) -> dict[str, Any]:
    return {"hook_event_name": "UserPromptSubmit", "session_id": SESSION,
            "transcript_path": str(transcript(tree)), "prompt": f"summarise {SECRET}"}


def test_t1_telemetry_is_off_by_default(user_run: Run, sink: Sink, tree: Tree) -> None:
    """Collector settings present, no switch: propose, verify, export, a state event and a
    gate event send nothing; doctor --json shows telemetry optional, off."""
    user_run.env |= sink.env(on=False)
    proj, work = f_chg(user_run)
    assert user_run.dl(work, "verify", "demo").returncode == 0
    (user_run.base / "home" / ".claude" / "dev-loop").mkdir(parents=True, exist_ok=True)
    at_context(tree, 150_000)
    assert gate(tree, event(tree), extra=user_run.env) is None  # writes agents.jsonl locally
    for argv in (("fleet-export",), ("fleet-export", "--delivery")):
        done = user_run.dl(proj, *argv)
        assert (done.returncode, done.stderr) == (0, ""), argv
    assert hook(user_run, SESSION_STATE, state_event(tree), {}) == ""
    assert sink.got == []
    done = user_run.dl(proj, "doctor", "--json")
    [row] = [one for one in json.loads(done.stdout) if one["name"] == "telemetry"]
    assert (row["status"], row["detail"]) == ("optional", "off"), row


def test_t3_no_prompt_file_text_or_secret_in_a_payload(
    user_run: Run, sink: Sink, tree: Tree
) -> None:
    """Telemetry on; the gate records a subagent, the exporter and the state hook send: no
    body holds the auth value, the host token, the prompt or the file text."""
    user_run.env |= sink.env(on=True)
    proj = f_local(user_run)
    at_context(tree, 150_000, prompt=f"open {SECRET}.pdf and copy it")
    assert gate(tree, event(tree), extra=user_run.env) is None
    done = user_run.dl(proj, "fleet-export")
    assert done.returncode == 0, done.stderr
    assert hook(user_run, SESSION_STATE, state_event(tree), {}) == ""
    paths = sorted(path for path, _, _ in sink.got)
    assert paths == ["/v1/logs", "/v1/metrics"], paths
    bodies = "\n".join(body for _, _, body in sink.got)
    for needle in (OTLP_SECRET, HOST_TOKEN, SECRET, "copy it", "summarise", "Bearer"):
        assert needle not in bodies, needle
    assert "dev_loop_subagent_context" in bodies
    assert any(OTLP_SECRET in head for _, head, _ in sink.got)  # it went as a header only


@pytest.fixture
def blackhole() -> Iterator[str]:
    """A collector that accepts the connection and never answers (not even the TLS handshake)."""
    silent = socket.socket()
    silent.bind(("127.0.0.1", 0))
    silent.listen(8)
    yield f"https://127.0.0.1:{silent.getsockname()[1]}"
    silent.close()


def test_t4_an_unreachable_collector_fails_in_time(user_run: Run, blackhole: str) -> None:
    """A blackholed endpoint: fleet-export ends under 60 s with one stderr line, exit 1."""
    folder = user_run.base / "home" / ".claude" / "dev-loop"
    folder.mkdir(parents=True)
    row = {"session_id": "s1", "agent_id": "a1", "agent_type": "t", "context_tokens": 9,
           "at": time.time()}
    (folder / "agents.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    user_run.env |= {"DEV_LOOP_TELEMETRY": "1", "OTEL_EXPORTER_OTLP_ENDPOINT": blackhole}
    started = time.monotonic()
    done = user_run.dl(user_run.base, "fleet-export")
    took = time.monotonic() - started
    assert done.returncode == 1, done.stderr
    failed = [said for said in done.stderr.splitlines() if "failed" in said]
    assert len(failed) == 1 and "OTLP push failed" in failed[0], done.stderr
    assert took < 60, took


def test_t4_the_gate_and_state_hook_never_wait_on_it(
    user_run: Run, blackhole: str, tree: Tree
) -> None:
    """With telemetry on and the collector blackholed the gate's verdicts are unchanged, and
    the state hook gives up within its 2 s with one line."""
    extra = {"DEV_LOOP_TELEMETRY": "1", "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
             "OTEL_LOGS_EXPORTER": "otlp", "OTEL_EXPORTER_OTLP_ENDPOINT": blackhole}
    at_context(tree, 179_999)
    assert gate(tree, event(tree), extra=extra) is None
    at_context(tree, 180_000)
    assert "180k is at 90% of its 200k cap" in denied(gate(tree, event(tree), extra=extra))
    started = time.monotonic()
    said = hook(user_run, SESSION_STATE, state_event(tree), extra)
    assert time.monotonic() - started < 10
    assert said.startswith("session-state: not sent:") and said.count("\n") == 1, said


def test_t5_two_tasks_are_tagged_each_on_its_own(home: Path) -> None:  # noqa: F811
    """Two tasks in one poll: each run's task label is its own; unset when off."""
    script = ["bash", str(worker.SCRIPT)]
    for switch, want in (("1", {f"5 {TAGS},dev_loop.task=5", f"6 {TAGS},dev_loop.task=6"}),
                         ("0", {"5 unset", "6 unset"})):
        (home / "runs").unlink(missing_ok=True)
        env = _env(home, TASKS_OUT="#5 #6", DEV_LOOP_TELEMETRY=switch)
        done = subprocess.run(
            script, env=env, capture_output=True, text=True, timeout=60, check=False)
        assert done.returncode == 0, done.stderr
        assert set(_read(home / "runs")) == want


def test_t6_six_megabytes_of_gate_lines_rotate_and_the_verdict_holds(tree: Tree) -> None:
    """agents.jsonl over 5 MB rolls to agents.1.jsonl on the next gate call; same verdicts."""
    folder = tree.root / "home" / ".claude" / "dev-loop"
    folder.mkdir(parents=True)
    row = json.dumps({"session_id": "s", "agent_id": "a", "agent_type": "t",
                      "context_tokens": 1, "at": 0.0}) + "\n"
    (folder / "agents.jsonl").write_text(row * (6 * 1024 * 1024 // len(row) + 1), "utf-8")
    at_context(tree, 179_999)
    assert gate(tree, event(tree)) is None
    assert (folder / "agents.1.jsonl").stat().st_size > 5 * 1024 * 1024
    assert len((folder / "agents.jsonl").read_text("utf-8").splitlines()) == 1
    at_context(tree, 180_000)
    assert "180k is at 90% of its 200k cap" in denied(gate(tree, event(tree)))


@pytest.mark.skipif(not Path("/proc/net/tcp").exists(), reason="reads Linux's socket table")
def test_t8_the_dashboard_serves_on_loopback_only(user_run: Run) -> None:
    """`dl dashboard` binds 127.0.0.1 alone and its page loads."""
    proj = f_local(user_run)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    served = subprocess.Popen(
        ["bash", str(PLUGIN / "bin" / "dl"), "dashboard"], cwd=proj,
        env=user_run.env | {"DL_PORT": str(port)},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        status = page(port)
        listening = [cols[1] for cols in (row.split() for row in
                     Path("/proc/net/tcp").read_text().splitlines()[1:]) if cols[3] == "0A"]
        mine = [where for where in listening if where.endswith(f":{port:04X}")]
        assert (status, mine) == (200, [f"0100007F:{port:04X}"])
        assert not [w for w in Path("/proc/net/tcp6").read_text().splitlines()
                    if f":{port:04X} " in w and " 0A " in w]
    finally:
        served.terminate()
        served.wait(timeout=30)


def page(port: int) -> int:
    for _ in range(100):
        try:
            url = f"http://127.0.0.1:{port}/"
            with urllib.request.urlopen(url, timeout=5) as answer:
                return int(answer.status)
        except OSError:
            time.sleep(0.2)
    return 0


def test_ft1_fast_track_is_off_by_default(user_run: Run) -> None:
    proj = f_local(user_run)
    done = user_run.dl(proj, "config", "get", "review.fast_track")
    assert (done.returncode, done.stdout.strip()) == (0, "false")


def records(user_run: Run, work: Path) -> None:
    """The records: one round (security blocker fixed, unlisted major as debt, minor
    deferred), its lesson, and the rest of the done rule's lines."""
    folder = work / ".dev-loop" / "changes" / "demo"
    (folder / "verify-report.md").write_text("green\n", "utf-8")
    (folder / "tasks.md").write_text("- [x] T1 demo\n", "utf-8")  # F-CHG's propose writes none
    clean_run(user_run, work)
    for argv in (
        ("round", "pr=7", "round=1", "base=-", "blocker=1", "major=1", "minor=1", "fixed=1",
         "deferred=1", "active=0", "turns=1", "seconds=1", "tokens_in=1", "tokens_out=1",
         "unmetered=0"),
        ("lesson", "priority=high", "summary=a token reached a log", "class=security",
         "guard=demo.txt", "files=demo.txt", "round=1", "avoid=never log a token"),
        ("debt", "pr=7", "thread=2", "file=demo.txt", "severity=major",
         "finding=a slow loop", "why=performance"),
        ("follow-up", "pr=7", "thread=3", "file=demo.txt", "finding=a name", "why=minor"),
        ("lessons-logged", "count=1"), ("verify", "result=green", "head=abc1234"),
        ("simplifier", "edits=0"), ("skills", "skills=review"),
        ("metrics", "pr=7", "change=demo", "rounds=1", "per_round=1/1/1", "total=1/1/1",
         "lessons=1"),
    ):
        done = user_run.dl(work, "record", "demo", *argv)
        assert done.returncode == 0, (argv, done.stderr)


def test_ft2_ft5_fast_track_records_and_the_done_rule(user_run: Run) -> None:
    """The records: the fix's lesson, the major as debt, the minor deferred; status shows the
    debt. `dl record done` names no missing lesson for the deferred major."""
    _proj, work = f_chg(user_run)
    assert user_run.dl(work, "config", "set", "review.fast_track", "true").returncode == 0
    records(user_run, work)
    folder = work / ".dev-loop" / "changes" / "demo"
    debt = (folder / "debt.md").read_text("utf-8")
    assert "| 7 | 2 | demo.txt | major | a slow loop | performance |" in debt
    assert "class: security" in (folder / "learnings.md").read_text("utf-8").lower()
    status = user_run.dl(work, "status").stdout
    assert "debt open: 1" in status, status
    done = user_run.dl(work, "record", "done")
    assert done.returncode == 0, done.stderr
    assert "[done?] demo" in done.stdout, done.stdout  # judged: its tasks are all ticked
    assert "Lessons logged" not in done.stdout, done.stdout


def test_ft5_a_fast_track_round_can_meet_the_done_rule(user_run: Run) -> None:
    """A fast-track round whose blocker is fixed and whose major is debt is the loop's end
    (one round, never a round 2), so the done rule does not ask for a clean last round. The
    integration branch's config decides fast track (v0.4.20 review minor 5)."""
    proj, work = f_chg(user_run)
    for where in (proj, work):
        assert user_run.dl(where, "config", "set", "review.fast_track", "true").returncode == 0
    user_run.git(proj, "commit", "-qam", "fast track")
    user_run.git(proj, "push", "-q", "origin", "main")
    records(user_run, work)
    done = user_run.dl(work, "record", "done")
    assert "a clean last review round" not in done.stdout, done.stdout
