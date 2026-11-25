"""Self-check for fleet/progress.py: rounds, findings and stages from change records."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from test_record import DONE, METRICS, ROUND, make, put

from dev_loop.fleet import progress


def rnd(pr: int, **n: int) -> dict[str, object]:
    return {"kind": "round", "pr": pr, "round": 1, "base": "x"} | n


OPEN, TICKED = "- [ ] a\n", "- [x] a\n"


def change(
    folder: Path, name: str, records: list[dict[str, object]], tasks: str | None = OPEN,
) -> None:
    (folder / name).mkdir(parents=True)
    if tasks:
        (folder / name / "tasks.md").write_text(tasks, encoding="utf-8")
    lines = "".join(json.dumps(r) + "\n" for r in records)
    (folder / name / "records.jsonl").write_text(lines, encoding="utf-8")


def build(tmp: Path) -> list[Path]:
    folder = tmp / "changes"
    change(folder, "alpha", [rnd(901, blocker=1, major=2, minor=0), rnd(901, minor=3)])
    change(folder, "beta", [rnd(902, major=1), rnd(999, major=9)])
    change(folder, "gamma", [], tasks=None)
    change(folder, "delta", [])  # a task list and no PR yet
    return sorted(folder.iterdir())


def test_rounds_findings_stages(tmp_path: Path) -> None:
    folder = build(tmp_path)
    prs = [{"pullRequestId": 901, "status": "active", "change": "alpha"},
           {"pullRequestId": 902, "status": "completed", "change": "beta"}]
    rounds = progress.rounds_of(prs, folder)
    assert {pr: len(v) for pr, v in rounds.items()} == {901: 2, 902: 1}  # 999 is not reported
    points = progress.points(prs, folder, rounds, tmp_path)
    assert ("pr_rounds", {"pr": "901"}, 2) in points
    assert ("pr_findings", {"pr": "901", "severity": "blocker"}, 1) in points
    assert ("pr_findings", {"pr": "901", "severity": "minor"}, 3) in points
    assert ("pr_findings", {"pr": "902", "severity": "major"}, 1) in points
    stages = {p[1]["change"]: p[1]["stage"] for p in points if p[0] == "change"}
    assert stages == {"alpha": "in_review", "beta": "merged", "gamma": "planned",
                      "delta": "building"}
    counts = {p[1]["stage"]: p[2] for p in points if p[0] == "changes"}
    assert counts == {"planned": 1, "building": 1, "in_review": 1, "merged": 1, "done": 0}
    assert progress.p50_rounds([prs[1]], rounds) == 1


def test_labels_are_ids_counts_stages(tmp_path: Path) -> None:
    folder = build(tmp_path)
    prs = [{"pullRequestId": 901, "status": "active", "change": "alpha", "title": "SECRET"}]
    for _, labels, _ in progress.points(prs, folder, progress.rounds_of(prs, folder), tmp_path):
        assert set(labels) <= {"pr", "severity", "change", "stage"}
        assert "SECRET" not in json.dumps(labels)


def test_empty_repository(tmp_path: Path) -> None:
    assert progress.points([], [], {}, tmp_path) == []
    assert progress.p50_rounds([], {}) is None


def git(root: Path, *args: str) -> str:
    argv = ["git", "-c", "user.name=t", "-c", "user.email=noreply@example.com", "-C", str(root)]
    return subprocess.run([*argv, *args], check=True, capture_output=True, text=True).stdout.strip()


def finished(root: Path, name: str) -> Path:
    """A change with every line of the done rule, verified green on the checkout's own head."""
    folder = make(root, name)
    put(root, name, "round", ROUND)
    put(root, name, "metrics", METRICS | {"change": name})
    head = {"head": git(root, "rev-parse", "HEAD")}  # a sha: another repository's git lacks it
    for kind, fields in DONE:
        put(root, name, kind, fields | (head if kind == "verify" else {}))
    return folder


def test_done_is_judged_against_the_checkout(
    root: Path, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """From cron the working directory is not the checkout; the done rule asks its git."""
    folder = finished(root, "x")
    other = tmp_path_factory.mktemp("other")
    git(other, "init", "-q")
    git(other, "commit", "-q", "--allow-empty", "-m", "another repository")
    monkeypatch.delenv("CLAUDE_PROJECT_DIR")
    monkeypatch.chdir(other)  # its git does not know the verified head
    assert progress.stage(folder, set(), root) == "done"
    monkeypatch.chdir(Path(other.anchor))  # no repository at all: project_root() would raise
    assert progress.stage(folder, set(), root) == "done"
    stages = [p[1]["stage"] for p in progress.points([], [folder], {}, root) if p[0] == "change"]
    assert stages == ["done"]


def test_a_change_merged_before_the_window_stays_merged(tmp_path: Path) -> None:
    """PRs are read for 30 days; an older merged change must not fall back to building."""
    folder = tmp_path / "changes"
    change(folder, "old", [rnd(700)], tasks=TICKED)  # reviewed, every task done, PR long closed
    change(folder, "open", [rnd(701)])  # reviewed once, tasks left: still being built
    change(folder, "unraised", [], tasks=TICKED)  # every task done, never a PR
    points = progress.points([], sorted(folder.iterdir()), {}, tmp_path)
    stages = {p[1]["change"]: p[1]["stage"] for p in points if p[0] == "change"}
    assert stages == {"old": "merged", "open": "building", "unraised": "building"}
    abandoned = {"abandoned"}  # a PR in the window says what happened: the records do not guess
    assert progress.stage(folder / "old", abandoned, tmp_path) == "building"


def test_points_take_the_rounds_already_read(tmp_path: Path) -> None:
    """The caller read the rounds for the week's p50; points reports those, reading none again."""
    given = {7: [rnd(7, major=2), rnd(7, minor=1)]}
    points = progress.points([], [], given, tmp_path)
    assert ("pr_rounds", {"pr": "7"}, 2) in points
    assert ("pr_findings", {"pr": "7", "severity": "major"}, 2) in points


def test_p50_is_the_median_of_the_round_counts() -> None:
    """PRs of 1, 2 and 5 rounds have a p50 of 2 (not their mean, and not a count of PRs)."""
    rounds = {pr: [rnd(pr)] * n for pr, n in ((1, 1), (2, 2), (3, 5))}
    prs = [{"pullRequestId": pr} for pr in (1, 2, 3, 4)]  # PR 4 was never reviewed: not a 0
    assert progress.p50_rounds(prs, rounds) == 2
