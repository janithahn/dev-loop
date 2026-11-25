"""Session state to Grafana: one OTLP/HTTP JSON log record per hook event.

SessionStart, UserPromptSubmit, Stop, Notification, PreCompact and SessionEnd each send the
session's state and its context (the last model call's prompt tokens, read by the dashboard
scanner's `call_of`) to the OTLP logs endpoint Claude Code itself uses, with its own resource
attributes so the record joins the session's metrics. Only the session id, a state word and a
count leave: never a prompt, a message, tool input or transcript text. Each send returns within
2 s; it exits 0 always and says a failure in one line on stderr (a hook fails open and
loud). After the state it sends the hooks' own errors (`dev_loop.gate_error`: hook, error class
and count per new batch of ~/.claude/dev-loop/hook-errors.jsonl and gate-errors.jsonl lines, by
a mark file beside them), so a hook that fails open is seen on the dashboard too.
With telemetry off (CLAUDE_CODE_ENABLE_TELEMETRY not 1, no otlp logs exporter, no endpoint) it
sends nothing and says nothing; on a gRPC protocol it sends nothing and says so (JSON needs HTTP).
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote
from urllib.request import Request, urlopen

STATES = {
    "SessionStart": "started",
    "UserPromptSubmit": "running",
    "Stop": "waiting",  # the turn ended: the session waits on a person
    "Notification": "waiting",  # a permission prompt, or idle at a question
    "PreCompact": "compacting",
    "SessionEnd": "ended",
}
EVENT = "dev_loop.session_state"
ERROR_EVENT = "dev_loop.gate_error"
TIMEOUT = 2
TAIL = 4 * 1024 * 1024  # the last model call is near the end; a longer last line leaves no count


def pairs(text: str) -> dict[str, str]:
    """Parse the OTel `key=value,key=value` list of the env variables; values are %-encoded."""
    found = (item.split("=", 1) for item in text.split(",") if "=" in item)
    return {key.strip(): unquote(value.strip()) for key, value in found}


def context_tokens(transcript: str) -> int | None:
    """Return the last assistant call's context, or None before the first call is written."""
    if not Path(transcript).exists():  # a new session's transcript is written at its first turn
        return None
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))  # this plugin's own
    from dev_loop.dashboard.scan import call_of  # noqa: PLC0415 - a failure is caught
    from dev_loop.dashboard.tail import USAGE  # noqa: PLC0415

    with Path(transcript).open("rb") as handle:
        start = handle.seek(max(0, handle.seek(0, os.SEEK_END) - TAIL))
        lines = handle.read().split(b"\n")[1 if start else 0 :]  # a cut first line is dropped
    for raw in reversed(lines):
        record = json.loads(raw) if USAGE in raw and b'"type":"assistant"' in raw else {}
        # top level only: a progress record nests a subagent's assistant message; a synthetic
        # call (an interrupt, an API error) has zero usage and the scanner keeps the last count
        if record.get("type") == "assistant" and (context := call_of(record).context):
            return int(context)
    return None


def attribute(key: str, value: str | int) -> dict[str, Any]:
    typed = {"intValue": str(value)} if isinstance(value, int) else {"stringValue": value}
    return {"key": key, "value": typed}


def envelope(name: str, session: str, records: list[list[dict[str, Any]]]) -> bytes:
    """Return the OTLP JSON body: a log record per attribute list, Claude Code's resource."""
    resource = pairs(os.environ.get("OTEL_RESOURCE_ATTRIBUTES", ""))
    resource["service.name"] = "claude-code"
    stamp = {"timeUnixNano": str(time.time_ns()), "body": {"stringValue": name}}
    first = [attribute("event.name", name), attribute("session.id", session)]
    scope = {"scope": {"name": name},
             "logRecords": [stamp | {"attributes": first + one} for one in records]}
    resource_attributes = [attribute(key, value) for key, value in resource.items()]
    logs = [{"resource": {"attributes": resource_attributes}, "scopeLogs": [scope]}]
    return json.dumps({"resourceLogs": logs}).encode()


def body(session: str, state: str, context: int | None) -> bytes:
    """Return the state's body: one log record, no text."""
    attributes = [attribute("state", state)]
    attributes += [] if context is None else [attribute("context_tokens", context)]
    return envelope(EVENT, session, [attributes])


def send_errors(session: str, endpoint: str, headers: dict[str, str]) -> None:
    """Send the hook and gate errors logged since the mark, a record per hook and error class.

    The mark moves after the send, so a batch that failed goes again with the next event.
    # ponytail: two sessions' events at once may each send a batch; a lock if the counts matter
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))  # this plugin's own
    from dev_loop.hookenv import errors_file, errors_since  # noqa: PLC0415 - a failure is caught

    mark, now = errors_file().with_name("errors-sent.mark"), time.time()
    try:
        since = float(mark.read_text("utf-8"))
    except (OSError, ValueError):
        since = 0.0  # no mark yet: what the logs hold is the first batch
    found = errors_since(since)
    if not found:
        return
    records = [[attribute("hook", hook), attribute("error", error), attribute("count", count)]
               for (hook, error), count in sorted(found.items())]
    data = envelope(ERROR_EVENT, session, records)
    urlopen(Request(endpoint, data, headers), timeout=TIMEOUT).close()  # noqa: S310
    mark.write_text(str(now), "utf-8")


def logs_endpoint() -> str | None:
    """Return Claude Code's OTLP logs URL, or None when its telemetry sends no logs."""
    env = os.environ.get
    if env("CLAUDE_CODE_ENABLE_TELEMETRY") != "1" or "otlp" not in env("OTEL_LOGS_EXPORTER", ""):
        return None
    base = env("OTEL_EXPORTER_OTLP_ENDPOINT", "").rstrip("/")
    return env("OTEL_EXPORTER_OTLP_LOGS_ENDPOINT") or (base and f"{base}/v1/logs") or None


def main() -> int:
    """Send one record; any failure is one stderr line naming its type, and exit 0."""
    try:
        endpoint = logs_endpoint()
        if not endpoint:
            return 0
        protocol = os.environ.get("OTEL_EXPORTER_OTLP_LOGS_PROTOCOL") or os.environ.get(
            "OTEL_EXPORTER_OTLP_PROTOCOL", ""
        )
        if protocol.startswith("grpc"):
            sys.stderr.write("session-state: not sent: OTLP JSON needs an http protocol\n")
            return 0
        event = json.load(sys.stdin)
        state = STATES[event["hook_event_name"]]
        try:
            context, note = context_tokens(event["transcript_path"]), ""
        except Exception as error:  # noqa: BLE001 - the state still goes; the gap is said
            context, note = None, f"session-state: context unread: {type(error).__name__}"
        headers = pairs(os.environ.get("OTEL_EXPORTER_OTLP_HEADERS", ""))
        headers |= pairs(os.environ.get("OTEL_EXPORTER_OTLP_LOGS_HEADERS", ""))
        headers["Content-Type"] = "application/json"
        data = body(str(event["session_id"]), state, context)
        urlopen(Request(endpoint, data, headers), timeout=TIMEOUT).close()  # noqa: S310
        send_errors(str(event["session_id"]), endpoint, headers)  # a failure: main's own line
    except Exception as error:  # noqa: BLE001 - a hook fails open and loud, never the session
        sys.stderr.write(f"session-state: not sent: {type(error).__name__}\n")
        return 0
    sys.stderr.write(note and f"{note}\n")
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except BaseException as error:  # noqa: BLE001 - what escapes main's except Exception
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from dev_loop.hookenv import crashed

        crashed("session-state", error, "not sent")  # a send failure stays main's own line
        code = 0
    sys.exit(code)
