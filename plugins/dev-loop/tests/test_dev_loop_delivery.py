"""Self-check for the delivery exporter.

Stubs every HTTP call (`transport.OPENER.open`: the host and the OTLP gateway), builds a checkout
and a repo list in a temporary directory, runs `fleet_export.main(["--delivery"])` and asserts
every series posted, the `repo` label on each, that no PR title, description, branch, author or
token reaches the batch, the worker rule as the repo list states it, and that a failed repo (host
down, token variable unset) is named on stderr by error class while the others still go.
"""

from __future__ import annotations

import io
import json
import os
import re
import urllib.error
import urllib.request
from contextlib import redirect_stderr
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest import mock

from dev_loop.fleet import delivery, fleet_export, progress, transport

NOW = 1_800_000_000.0  # 2027-01-15T08:00:00Z
TOKEN, OTLP_SECRET = "pat-secret-123", "otlp-secret-456"  # noqa: S105 - fakes
COORD = "noreply@coord.test"
SECRETS = (TOKEN, OTLP_SECRET, "SECRET-TITLE", "SECRET-BODY", "noreply", "feature/beta", "alpha-b")
SIGNED = "SECRET-BODY\n[worker worker-a] raised this"  # the mark on a later line
MENTION = "SECRET-BODY, installed on worker-1"  # names a machine: not a worker's PR


ENV = {
    "DEV_LOOP_TELEMETRY": "1",
    "DEV_LOOP_HOST_TOKEN": TOKEN,
    "OTEL_EXPORTER_OTLP_ENDPOINT": "https://otlp.test/otlp/",
    "OTEL_EXPORTER_OTLP_HEADERS": f"Authorization=Basic%20{OTLP_SECRET}",
}


def stamp(hours_ago: float) -> str:
    seconds = NOW - hours_ago * 3600
    return datetime.fromtimestamp(seconds, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.1234567Z")


def pull(number: int, status: str, source: str, **extra: Any) -> dict[str, Any]:  # noqa: ANN401
    return {
        "pullRequestId": number,
        "status": status,
        "sourceRefName": f"refs/heads/{source}",
        "targetRefName": f"refs/heads/{extra.pop('target', 'main')}",
        "title": "SECRET-TITLE",
        "description": extra.pop("description", "SECRET-BODY"),
        "createdBy": {"uniqueName": extra.pop("by", COORD), "displayName": "SECRET-TITLE"},
        "creationDate": stamp(extra.pop("created")),
        **({"closedDate": stamp(extra.pop("closed"))} if "closed" in extra else {}),
    }


ACTIVE = [
    pull(
        901,
        "active",
        "feature/alpha-b",
        target="feature/alpha-a",
        description="[worker worker-1] SECRET-BODY",
        created=10,
    ),
    pull(
        904, "active", "feature/beta-b", by="noreply@x.test", created=1, description=SIGNED
    ),
]
CLOSED = [
    pull(902, "completed", "feature/beta", created=30, closed=6, description=MENTION),
    pull(903, "abandoned", "fix/typo", by="noreply@y.test", created=50, closed=48),
]


class Fake:
    """Records each request and answers it as ADO and the OTLP gateway would."""

    def __init__(self) -> None:
        self.requests: list[urllib.request.Request] = []

    def __call__(self, request: urllib.request.Request, timeout: float) -> io.BytesIO:
        assert timeout <= 60, timeout
        self.requests.append(request)
        url = request.full_url
        if "dev.azure.com/down/" in url:
            raise urllib.error.URLError("unreachable")
        if not url.startswith("https://otlp.test"):
            assert request.get_header("Authorization") == "Basic OnBhdC1zZWNyZXQtMTIz", url
        return io.BytesIO(json.dumps(self.answer(url)).encode())

    @staticmethod
    def answer(url: str) -> Any:  # noqa: ANN401 - JSON
        if url.startswith("https://otlp.test"):
            return {}
        assert "/pullrequests?" in url, url
        return {"value": ACTIVE if "status=active" in url else CLOSED}

    def posted(self) -> dict[str, Any]:
        sent = [r for r in self.requests if r.full_url.startswith("https://otlp.test")]
        assert len(sent) == 1, sent
        assert isinstance(sent[0].data, bytes)
        result: dict[str, Any] = json.loads(sent[0].data)
        return result


def run(fake: Fake, **env: str) -> tuple[int, str]:
    """Run main(--delivery) once under the stubs; return its exit code and its stderr."""
    errors = io.StringIO()
    with (
        mock.patch.object(transport.OPENER, "open", fake),
        mock.patch.object(fleet_export.time, "time", lambda: NOW),
        mock.patch.dict(os.environ, ENV | env),
        redirect_stderr(errors),
    ):
        code = fleet_export.main(["--delivery", "--host", "box-1"])
    return code, errors.getvalue()


Series = dict[str, list[tuple[tuple[tuple[str, str], ...], float]]]


def series(sent: dict[str, Any]) -> Series:
    """Each metric's points as sorted (labels, value) pairs; every point must carry the repo."""
    found: Series = {}
    for metric in sent["resourceMetrics"][0]["scopeMetrics"][0]["metrics"]:
        for point in metric["gauge"]["dataPoints"]:
            labels = {a["key"]: a["value"]["stringValue"] for a in point["attributes"]}
            assert labels.pop("host") == "box-1", labels
            assert labels.pop("repo") == "repo-x", labels
            found.setdefault(metric["name"], []).append(
                (tuple(sorted(labels.items())), point["asDouble"])
            )
    return {name: sorted(points) for name, points in found.items()}


def expect(**metrics: list[tuple[dict[str, str], float]]) -> Series:
    return {
        f"dev_loop_{name}": sorted((tuple(sorted(labels.items())), value) for labels, value in rows)
        for name, rows in metrics.items()
    }


EXPECTED = expect(
    prs=[
        (
            {
                "state": "active",
                "opened_by": "worker",
                "target": "feature/alpha-a",
            },
            1.0,
        ),
        (
            {"state": "completed", "opened_by": "coordinator", "target": "main"},
            1.0,
        ),
        ({"state": "abandoned", "opened_by": "teammate", "target": "main"}, 1.0),
        ({"state": "active", "opened_by": "worker", "target": "main"}, 1.0),
    ],
    pr=[
        (
            {
                "pr": "901",
                "target": "feature/alpha-a",
                "state": "active",
                "change": "alpha",
                "opened_by": "worker",
            },
            10.0,
        ),
        (
            {
                "pr": "902",
                "target": "main",
                "state": "completed",
                "change": "beta",
                "opened_by": "coordinator",
            },
            24.0,
        ),
        (
            {
                "pr": "903",
                "target": "main",
                "state": "abandoned",
                "change": "",
                "opened_by": "teammate",
            },
            2.0,
        ),
        (  # opened by a worker: its description's mark, not its author, says so
            {
                "pr": "904",
                "target": "main",
                "state": "active",
                "change": "beta",
                "opened_by": "worker",
            },
            1.0,
        ),
    ],
    merged_week=[({}, 1.0)],
    pr_rounds=[({"pr": "902"}, 1.0)],  # the one round on record, of the week's merged PR
    pr_findings=[({"pr": "902", "severity": "blocker"}, 0.0),
                 ({"pr": "902", "severity": "major"}, 2.0),
                 ({"pr": "902", "severity": "minor"}, 1.0)],
    review_rounds_p50=[({}, 1.0)],
    lead_time_hours_p50=[({}, 24.0)],
    change=[
        ({"change": "alpha", "stage": "in_review"}, 1.0),
        ({"change": "beta", "stage": "in_review"}, 1.0),
    ],
    changes=[({"stage": at}, 2.0 if at == "in_review" else 0.0) for at in progress.STAGES],
)


def checkout(root: Path) -> Path:
    """A checkout: the two change folders the PR branches name, and one review round on record."""
    repo = root / "checkout"
    for name in ("alpha", "beta"):
        (repo / ".dev-loop" / "changes" / name).mkdir(parents=True)
    reviewed = {"kind": "round", "pr": 902, "round": 1, "base": "full", "major": 2, "minor": 1}
    (repo / ".dev-loop" / "changes" / "beta" / "records.jsonl").write_text(
        json.dumps(reviewed), encoding="utf-8")
    return repo


def test_delivery(tmp_path: Path) -> None:
    repo = {
        "repo": "repo-x",
        "org": "org-x",
        "project": "proj-y",
        "checkout": str(checkout(tmp_path)),
        "coordinator": COORD,
        "worker_pattern": r"^\[worker [a-z0-9-]+\]",
    }
    repos = tmp_path / "repos.json"
    with mock.patch.object(delivery, "REPOS", repos):
        fake = Fake()  # no repo list: skipped and named, nothing pushed, not a failure
        code, errors = run(fake)
        assert (code, fake.requests) == (0, []) and "delivery: skipped" in errors, errors

        repos.write_text(json.dumps({"repos": [repo]}), encoding="utf-8")
        for _ in range(2):  # a second run sends the same batch
            fake = Fake()
            code, errors = run(fake)
            assert (code, errors) == (0, ""), (code, errors)
            assert series(fake.posted()) == EXPECTED, series(fake.posted())
            text = json.dumps(fake.posted())
            for secret in SECRETS:
                assert secret not in text, secret

        down = repo | {"repo": "DOWN", "org": "down"}
        repos.write_text(json.dumps({"repos": [down, repo]}), encoding="utf-8")
        fake = Fake()
        code, errors = run(fake)
        assert code == 1 and errors.count("\n") == 1 and "DOWN" in errors, errors
        assert "URLError" in errors and TOKEN not in errors, errors
        assert series(fake.posted()) == EXPECTED  # the healthy repo still goes

        tokenless = repo | {"repo": "BARE", "org": "org-z", "token_env": "UNSET_TOKEN_VAR"}
        repos.write_text(json.dumps({"repos": [tokenless, repo]}), encoding="utf-8")
        fake = Fake()
        code, errors = run(fake)
        assert code == 1 and "BARE failed (KeyError)" in errors, errors
        assert not [r for r in fake.requests if "/org-z/" in r.full_url], fake.requests

        pr = {"description": "[worker worker-1]", "createdBy": {}}  # no worker_pattern: no worker
        assert delivery.opened_by({}, pr) == "teammate"
        bad = repo | {"worker_pattern": "[unclosed"}
        repos.write_text(json.dumps({"repos": [bad]}), encoding="utf-8")
        code, errors = run(Fake())
        assert code == 1 and f"repo-x failed ({re.error.__name__})" in errors, errors


def test_off_by_default(tmp_path: Path) -> None:
    fake = Fake()
    with mock.patch.object(delivery, "REPOS", tmp_path / "none.json"):
        code, errors = run(fake, DEV_LOOP_TELEMETRY="")
    assert (code, errors, fake.requests) == (0, "", [])
