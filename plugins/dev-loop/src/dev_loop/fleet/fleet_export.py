"""Push the fleet's worker load and Claude account usage as OTLP gauges: `dl fleet-export`.

Off unless `DEV_LOOP_TELEMETRY=1`. One OTLP/HTTP JSON batch, over https and never redirected, to
`<OTEL_EXPORTER_OTLP_ENDPOINT>/v1/metrics` with `OTEL_EXPORTER_OTLP_HEADERS`, both from the
environment. Worker (`DEV_LOOP_WORKER`, `DEV_LOOP_ORG`, `DEV_LOOP_PROJECT`, token in
`DEV_LOOP_HOST_TOKEN`): slots, lock dirs, tasks (tag `worker-<name>`) by state, open QUESTIONs.
Accounts (`cswap list --json`): 5h/7d use, reset, active here. Subagents (the gate's agents.jsonl):
each live subagent's latest context, by session, id and type. Every series carries `host` and
`repo` (`DEV_LOOP_REPO`, else `fleet`); ids, counts and error classes only, never a token, title,
comment or email. A failed source or endpoint: one stderr line, exit 1; nothing retried.
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from . import delivery
from .transport import fetch

RUN = Path.home() / ".agent-worker-run"
AGENTS = Path.home() / ".claude" / "dev-loop" / "agents.jsonl"  # the context gate's telemetry
SUBAGENT_WINDOW = 30 * 60  # seconds: the latest line per agent in the last half hour
CSWAP = ["uv", "tool", "run", "--from", "claude-swap>=0.26,<0.27", "cswap", "list", "--json"]
STATES = {"To Do": "todo", "Doing": "doing"}
WINDOWS = {"fiveHour": "5h", "sevenDay": "7d"}
Point = tuple[str, dict[str, str], float]
BROKEN = (OSError, ValueError, LookupError, TypeError, AttributeError)  # unreachable or malformed


def say(line: str) -> None:
    print(f"fleet_export: {line}", file=sys.stderr)  # noqa: T201 - cron mails stderr


def azdo() -> tuple[str, str]:
    """Return the work-item API base and its auth header, from the environment."""
    env = os.environ
    base = f"https://dev.azure.com/{env['DEV_LOOP_ORG']}/{env['DEV_LOOP_PROJECT']}/_apis/wit"
    return base, "Basic " + base64.b64encode(f":{env[delivery.TOKEN_ENV]}".encode()).decode()


def ado(path: str, body: object = None) -> Any:  # noqa: ANN401 - JSON
    base, auth = azdo()
    data = None if body is None else json.dumps(body).encode()
    headers = {"Authorization": auth, "Content-Type": "application/json"}
    return fetch(urllib.request.Request(base + path, data=data, headers=headers))  # noqa: S310


def asked(name: str, item: str) -> bool:
    """Say whether the task's newest comment is this worker's QUESTION (none answered it yet)."""
    found = ado(f"/workItems/{item}/comments?api-version=7.1-preview.4")["comments"]
    newest = max(found, key=lambda comment: comment["id"], default={"text": ""})
    text = html.unescape(re.sub(r"<[^>]+>", "", newest["text"] or "")).strip()
    return text.startswith(f"[worker {name}] QUESTION")


def worker_points(name: str) -> list[Point]:
    if not re.fullmatch(r"[a-z0-9-]+", name):  # it goes into a WIQL string
        msg = "DEV_LOOP_WORKER: lowercase letters, digits and - only"
        raise ValueError(msg)
    query = (  # WIQL; the name is checked above
        "SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = @project"  # noqa: S608
        f" AND [System.Tags] CONTAINS 'worker-{name}' AND [System.State] IN ('To Do', 'Doing')"
    )
    ids = [str(item["id"]) for item in ado("/wiql?api-version=7.1", {"query": query})["workItems"]]
    path = f"/workitems?ids={','.join(ids)}&fields=System.State&api-version=7.1"
    states = [STATES[item["fields"]["System.State"]] for item in ado(path)["value"]] if ids else []
    running = sum(lock.is_dir() for lock in RUN.iterdir()) if RUN.is_dir() else 0
    label = {"worker": name}
    return [
        ("worker_slots", label, float(os.environ.get("SLOTS", "3"))),  # the runner's default
        ("worker_tasks", label | {"state": "running"}, running),
        ("worker_tasks", label | {"state": "todo"}, states.count("todo")),
        ("worker_tasks", label | {"state": "doing"}, states.count("doing")),
        ("worker_questions", label, sum(asked(name, item) for item in ids)),
    ]


def account_points(now: float) -> list[Point]:
    """Each account's use and reset per window, and which is active here; no reading, no sample."""
    options = {"capture_output": True, "text": True, "check": True, "timeout": 120}
    run = subprocess.run(CSWAP, **options)  # type: ignore[call-overload]  # noqa: S603
    answer = json.loads(run.stdout)
    points: list[Point] = []
    for one in answer["accounts"]:
        label = {"account": str(one["number"])}
        active = one["number"] == answer["activeAccountNumber"]
        points.append(("account_active", label, float(active)))
        for key, window in WINDOWS.items():
            raw = (one.get("usage") or {}).get(key) or {}
            if raw.get("pct") is not None:
                points.append(("account_usage_ratio", label | {"window": window}, raw["pct"] / 100))
            if raw.get("resetsAt") is not None:
                left = datetime.fromisoformat(raw["resetsAt"]).timestamp() - now
                points.append(("account_reset_seconds", label | {"window": window}, max(left, 0.0)))
    return points


def subagent_rows(now: float) -> list[dict[str, Any]]:
    """Each well-formed agents.jsonl line from the last 30 min: an id, a time and a count."""
    if not AGENTS.exists():
        return []
    kept: list[dict[str, Any]] = []
    for raw in AGENTS.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(row, dict):  # valid JSON that is not an object (e.g. `[1]`): dropped
            continue
        at, agent, context = row.get("at"), row.get("agent_id"), row.get("context_tokens")
        typed = isinstance(agent, str) and isinstance(at, (int, float))
        if typed and isinstance(context, (int, float)) and at >= now - SUBAGENT_WINDOW:
            kept.append(row)
    return kept


def subagent_points(now: float) -> list[Point]:
    """One point per live subagent: its latest context, labelled by its ids and type (repo added)."""
    latest: dict[str, dict[str, Any]] = {}
    for row in subagent_rows(now):
        agent = row["agent_id"]
        if agent not in latest or row["at"] >= latest[agent]["at"]:
            latest[agent] = row
    points: list[Point] = []
    for row in latest.values():
        labels = {
            "session_id": str(row.get("session_id") or ""),
            "agent_id": str(row["agent_id"]),
            "agent_type": str(row.get("agent_type") or ""),
        }
        points.append(("subagent_context", labels, float(row["context_tokens"])))
    return points


def gather(now: float) -> tuple[list[Point], bool]:
    """Every group's points, and False when a group's source failed (said on stderr)."""
    points, ok = list[Point](), True
    worker = os.environ.get("DEV_LOOP_WORKER", "")
    if not worker:
        say("workers: skipped, DEV_LOOP_WORKER is not set")
    else:
        try:
            points += worker_points(worker)
        except BROKEN as error:
            ok = False
            say(f"workers: work items failed ({type(error).__name__})")
    try:
        points += account_points(now)
    except FileNotFoundError:
        say("accounts: skipped, uv (for cswap) is not installed")
    except (*BROKEN, subprocess.SubprocessError) as error:
        ok = False
        say(f"accounts: cswap failed ({type(error).__name__})")
    try:
        points += subagent_points(now)
    except BROKEN as error:
        ok = False
        say(f"subagents: agents.jsonl failed ({type(error).__name__})")
    return points, ok


def attributes(labels: dict[str, str]) -> list[dict[str, Any]]:
    return [{"key": key, "value": {"stringValue": value}} for key, value in labels.items()]


def body(points: list[Point], host: str, now: float) -> dict[str, Any]:
    """Return the OTLP JSON export request: one gauge per name, every point labelled by host."""
    stamp = str(int(now * 1e9))
    metrics: dict[str, list[dict[str, Any]]] = {}
    for name, labels, value in points:
        row = {"attributes": attributes(labels | {"host": host}), "timeUnixNano": stamp}
        metrics.setdefault(f"dev_loop_{name}", []).append(row | {"asDouble": float(value)})
    gauges = [{"name": name, "gauge": {"dataPoints": rows}} for name, rows in metrics.items()]
    resource = {"attributes": attributes({"service.name": "dev-loop", "host.name": host})}
    scope = {"scope": {"name": "dev-loop.fleet"}, "metrics": gauges}
    return {"resourceMetrics": [{"resource": resource, "scopeMetrics": [scope]}]}


def push(payload: dict[str, Any]) -> None:
    """POST the batch once to the user's endpoint; a partial rejection raises."""
    found = os.environ
    headers = {"Content-Type": "application/json"}
    for pair in filter(None, found.get("OTEL_EXPORTER_OTLP_HEADERS", "").split(",")):
        key, _, value = pair.partition("=")
        headers[key.strip()] = urllib.parse.unquote(value.strip())
    url = found["OTEL_EXPORTER_OTLP_ENDPOINT"].rstrip("/") + "/v1/metrics"
    request = urllib.request.Request(url, json.dumps(payload).encode(), headers)  # noqa: S310
    rejected = fetch(request).get("partialSuccess", {}).get("rejectedDataPoints", 0)
    if int(rejected):
        msg = f"{rejected} data points rejected"
        raise ValueError(msg)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Push the fleet's gauges to Grafana once.")
    parser.add_argument("--host", default=socket.gethostname())
    parser.add_argument("--delivery", action="store_true", help="PR series per listed repo")
    args = parser.parse_args(argv)
    if os.environ.get("DEV_LOOP_TELEMETRY") != "1":
        return 0  # export is off until the user enables telemetry
    host, now = args.host, time.time()
    points, ok = delivery.gather(now, say) if args.delivery else gather(now)
    repo = os.environ.get("DEV_LOOP_REPO", "fleet")
    points = [(name, {"repo": repo} | labels, value) for name, labels, value in points]
    if points:
        try:
            push(body(points, host, now))
        except BROKEN as error:
            say(f"OTLP push failed ({type(error).__name__})")
            return 1
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
