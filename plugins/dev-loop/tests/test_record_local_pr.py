"""A local host's PR id (12 lowercase hex) is a PR id the record writer and readers accept."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_record import FOLLOW_UP, METRICS, ROUND, make, put

from dev_loop.records import record
from dev_loop.records.review_records import metric_rows

LOCAL = "c2425df1f07d"


def test_local_id_writes_follow_up_and_round(
    root: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    """`dl record <change> follow-up|round pr=<12 hex>` writes Markdown and records.jsonl."""
    folder = make(root, "x")
    for kind, fields in (("follow-up", FOLLOW_UP), ("round", ROUND)):
        pairs = [f"{k}={v}" for k, v in fields.items()]
        assert record.main(["x", kind, *pairs, f"pr={LOCAL}"]) == 0
    assert "refused" not in capsys.readouterr().err
    assert f"| {LOCAL} | 70 | `a.py` | f | w |" in (folder / "follow-ups.md").read_text("utf-8")
    assert f"(PR {LOCAL}, base full)" in (folder / "status.md").read_text("utf-8")
    sidecar = [json.loads(x) for x in (folder / "records.jsonl").read_text("utf-8").splitlines()]
    assert [(r["kind"], r["pr"]) for r in sidecar] == [("follow-up", LOCAL), ("round", LOCAL)]
    assert record.unparsed(root) == []


def test_numeric_id_still_an_int(root: Path) -> None:
    """An ADO or GitHub id stays the number it always was."""
    folder = make(root, "x")
    put(root, "x", "follow-up", FOLLOW_UP)
    assert record.read(folder)[0]["pr"] == 7


@pytest.mark.parametrize(
    "bad", ["abc", "C2425DF1F07D", "c2425df1f07", "c2425df1f07d0", "-7", "7a"])
def test_other_ids_refused(root: Path, bad: str) -> None:
    """An id that is neither digits nor 12 lowercase hex is still refused, nothing written."""
    folder = make(root, "x")
    with pytest.raises(ValueError, match="bad pr"):
        put(root, "x", "round", ROUND | {"pr": bad})
    with pytest.raises(ValueError, match="bad cause_pr"):
        put(root, "x", "rework", {"pr": "7", "cause_pr": bad, "note": "n"})
    assert not (folder / "records.jsonl").exists()


def test_readers_sort_and_group_mixed_ids(root: Path) -> None:
    """Metrics rows and the done rule order numeric and local ids together without a crash."""
    folder = make(root, "x")
    for pr in ("12", LOCAL, "7", "0a0a0a0a0a0a"):
        put(root, "x", "metrics", METRICS | {"pr": pr})
    assert [row.split(" | ")[0] for row in metric_rows(root)] == [
        "| 7", "| 12", "| 0a0a0a0a0a0a", f"| {LOCAL}"]
    put(root, "x", "round", ROUND | {"pr": "99"})
    put(root, "x", "round", ROUND | {"pr": "ffffffffffff"})
    gaps = record.missing(folder)
    assert "a metrics row for PR 99" in gaps and "a metrics row for PR ffffffffffff" in gaps, gaps


def test_local_id_lines_are_record_lines(root: Path) -> None:
    """Models, Context and Rework lines with a hex id starting with a letter are record lines."""
    make(root, "x")
    put(root, "x", "models", {"pr": LOCAL, "builder": "a", "reviewer": "b"})
    put(root, "x", "context", {"pr": LOCAL, "peak": "1", "handovers": "0", "stops": "0"})
    put(root, "x", "rework", {"pr": LOCAL, "cause_pr": "0a0a0a0a0a0a", "note": "n"})
    assert record.unparsed(root) == []


def test_a_follow_up_found_before_a_pr_takes_pr_none(root: Path) -> None:
    """`pr=none` is a follow-up's pr when no PR exists yet; no other kind takes it."""
    folder = make(root, "x")
    assert record.main(["x", "follow-up", *[f"{k}={v}" for k, v in FOLLOW_UP.items() if k != "pr"],
                        "pr=none"]) == 0
    assert "| none | 70 | `a.py` | f | w |" in (folder / "follow-ups.md").read_text("utf-8")
    assert record.unparsed(root) == []
    with pytest.raises(ValueError, match="bad pr"):
        put(root, "x", "round", ROUND | {"pr": "none"})
