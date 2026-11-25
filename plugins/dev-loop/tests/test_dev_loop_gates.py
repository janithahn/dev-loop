"""Self-check for the gate series: merge without duplicates, drop the old, report a failed source."""

from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from unittest import mock

from dev_loop.fleet import delivery, gates

NOW = 1_800_000_000.0
REPO = {"repo": "repo-x", "org": "o", "project": "p", "pipeline": 7}


def stamp(days_ago: float) -> str:
    return gates.datetime.fromtimestamp(NOW - days_ago * 86400, tz=gates.UTC).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def build(ident: int, result: str, ended: float, minutes: int = 6, ref: str | None = None) -> Any:  # noqa: ANN401
    return {"id": ident, "result": result, "finishTime": stamp(ended),
            "startTime": stamp(ended + minutes / 1440),
            "sourceBranch": ref or f"refs/pull/{ident}/merge"}


def get_of(builds: list[Any]) -> Any:  # noqa: ANN401
    return lambda _repo, _path: {"value": builds}


def test_runs_merge_without_duplicates(tmp_path: Path) -> None:
    said: list[str] = []
    with mock.patch.object(gates, "HOME", tmp_path):
        builds = [build(1, "succeeded", 1), build(2, "failed", 2, 10),
                  build(3, "succeeded", 3, ref="refs/heads/main"), build(4, "inProgress", 0)]
        builds[3]["result"] = None
        for _ in range(2):
            found = gates.points(REPO, get_of(builds), NOW, said.append)
        counts = {p[1]["result"]: p[2] for p in found if p[0] == "gate_runs"}
        assert counts == {"succeeded": 1, "failed": 1, "canceled": 0, "partiallySucceeded": 0}
        assert ("gate_run_minutes", {"pr": "2", "build": "2", "result": "failed"}, 10.0) in found
        assert len(gates.history(REPO).read_text().splitlines()) == 2


def test_a_rerun_with_the_same_result_is_its_own_series(tmp_path: Path) -> None:
    """One PR's gate run twice with one result is two points, never one label set twice."""
    reruns = [build(n, "failed", 1, ref="refs/pull/5/merge") for n in (11, 12)]
    with mock.patch.object(gates, "HOME", tmp_path):
        found = gates.points(REPO, get_of(reruns), NOW, print)
    minutes = [labels for name, labels, _ in found if name == "gate_run_minutes"]
    assert minutes == [{"pr": "5", "build": "11", "result": "failed"},
                       {"pr": "5", "build": "12", "result": "failed"}], minutes


def test_a_run_that_never_started_is_counted_without_minutes(tmp_path: Path) -> None:
    """Canceled while queued, a build has no startTime: counted, with no duration point."""
    queued = build(21, "canceled", 1)
    del queued["startTime"]
    with mock.patch.object(gates, "HOME", tmp_path):
        for _ in range(2):  # the second run reads it back from the kept history
            found = gates.points(REPO, get_of([queued, build(22, "succeeded", 2)]), NOW, print)
            counts = {p[1]["result"]: p[2] for p in found if p[0] == "gate_runs" and p[2]}
            assert counts == {"canceled": 1, "succeeded": 1}, found
            assert [p[1]["build"] for p in found if p[0] == "gate_run_minutes"] == ["22"], found


def test_old_entries_drop(tmp_path: Path) -> None:
    with mock.patch.object(gates, "HOME", tmp_path):
        old = {"build": 9, "pr": 9, "result": "failed", "finished": NOW - 401 * 86400,
               "duration": 60.0}
        recent = old | {"build": 8, "finished": NOW - 100 * 86400}
        gates.history(REPO).write_text(
            "\n".join(map(json.dumps, [old, recent])) + "\n")
        found = gates.points(REPO, get_of([]), NOW, print)
        kept = gates.history(REPO).read_text().splitlines()
        assert [json.loads(k)["build"] for k in kept] == [8]
        # kept but outside the 30-day window: the source was read, so each result is a stated 0
        assert found == [("gate_runs", {"result": r}, 0) for r in gates.RESULTS]


def test_a_null_pipeline_is_no_pipeline(tmp_path: Path) -> None:
    """A row written `"pipeline": null` has no gate to read: nothing fetched, nothing said."""
    said: list[str] = []
    with mock.patch.object(gates, "HOME", tmp_path):
        for row in (REPO | {"pipeline": None}, REPO | {"pipeline": None, "provider": "github"}):
            assert gates.points(row, get_of([build(1, "failed", 1)]), NOW, said.append) == []
    assert said == [] and not list(tmp_path.iterdir())


def test_a_partial_success_is_a_finished_run(tmp_path: Path) -> None:
    with mock.patch.object(gates, "HOME", tmp_path):
        found = gates.points(REPO, get_of([build(31, "partiallySucceeded", 1)]), NOW, print)
    assert ("gate_runs", {"result": "partiallySucceeded"}, 1) in found, found


def test_failing_source_reported_rest_exports(tmp_path: Path) -> None:
    repos = tmp_path / "repos.json"
    bad = REPO | {"repo": "BAD"}
    repos.write_text(json.dumps({"repos": [bad, REPO | {"provider": "github"}]}))
    said: list[str] = []

    def get(repo: Any, _path: str) -> Any:  # noqa: ANN401
        if repo["repo"] == "BAD":
            raise OSError("down")
        return {"value": []}

    with (mock.patch.object(delivery, "REPOS", repos), mock.patch.object(gates, "HOME", tmp_path),
          mock.patch.object(delivery, "repo_points", lambda repo, _now, _seen: [("prs", {}, 1.0)]),
          mock.patch.object(delivery, "ado", get)):
        points, ok = delivery.gather(NOW, said.append)
    assert not ok and any("BAD" in s and "OSError" in s for s in said), said
    assert any("not run" in s for s in said), said
    assert [p[1]["repo"] for p in points] == ["BAD", "repo-x"]  # PR points kept for both


def test_export_imports_where_fcntl_is_absent() -> None:
    """A Windows host has no fcntl; `dl fleet-export` must still import its whole chain."""
    code = "import sys; sys.modules['fcntl'] = None; import dev_loop.fleet.fleet_export"
    src = str(Path(gates.__file__).resolve().parents[2])
    done = subprocess.run([sys.executable, "-c", code], env={"PYTHONPATH": src},
                          capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr[-300:]


def test_one_history_per_org_project_and_repo(tmp_path: Path) -> None:
    """Two projects may each hold a repository of one name: their runs never share a history."""
    twin = REPO | {"project": "q"}
    with mock.patch.object(gates, "HOME", tmp_path):
        assert gates.history(REPO) != gates.history(twin)
        assert gates.history(REPO).parent == tmp_path
        gates.points(REPO, get_of([build(1, "failed", 1)]), NOW, print)
        found = gates.points(twin, get_of([build(1, "succeeded", 1)]), NOW, print)
    assert ("gate_runs", {"result": "succeeded"}, 1) in found, found  # build 1 is not "known"
    assert ("gate_runs", {"result": "failed"}, 0) in found, found


def test_overlapping_merges_lose_no_run(tmp_path: Path) -> None:
    """Crons that overlap each read, add and replace the history: under the lock, none is lost."""
    with mock.patch.object(gates, "HOME", tmp_path), ThreadPoolExecutor(12) as pool:
        list(pool.map(lambda n: gates.merged(REPO, [build(n, "failed", 1)], NOW), range(48)))
        kept = [json.loads(line)["build"] for line in gates.history(REPO).read_text().splitlines()]
    assert sorted(kept) == list(range(48))
    assert [p.name for p in tmp_path.iterdir()] == [gates.history(REPO).name]  # no lock, no draft
