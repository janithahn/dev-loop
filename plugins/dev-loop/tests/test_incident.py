"""`dl incident`: a loop incident is refused without its fields; a good one is counted like a lesson."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import test_neutral
from dev_loop.records import incident, record
from dev_loop.records.review_lessons import iso_week, lesson_counts, trend

FIELDS = {"what": "a build lost on a full disk", "impact": "one row rebuilt",
          "root_cause": "no disk check", "guard": "tests/g.py"}


def args(change: str = "c", **over: str) -> list[str]:
    fields = FIELDS | over
    return [change, *(f"{key}={value}" for key, value in fields.items() if value != "-")]


def change(root: Path, name: str = "c") -> Path:
    folder = record.changes_dir(root) / name
    folder.mkdir(parents=True)
    return folder


def test_an_incident_writes_both_files(root: Path) -> None:
    folder = change(root)
    assert incident.main(args()) == 0
    [written] = [json.loads(x) for x in (folder / "records.jsonl").read_text().splitlines()]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")  # noqa: UP017 - 3.10 hosts
    assert written["kind"] == "incident"
    assert (written["root_cause"], written["date"]) == ("no disk check", today)
    text = (folder / "incidents.md").read_text()
    assert text.startswith("# c incidents\n")
    assert written["line"] in text
    assert record.unparsed(root) == []


@pytest.mark.parametrize("name", ["what", "impact", "root_cause", "guard"])
def test_an_incomplete_incident_is_refused_naming_the_field(
    root: Path, name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = change(root)
    assert incident.main(args(**{name: "-"})) == 1
    err = capsys.readouterr().err
    assert err.startswith("dl incident: refused: ")
    assert f"missing {name}" in err
    assert not (folder / "records.jsonl").exists()
    assert not (folder / "incidents.md").exists()


def test_a_given_date_or_unknown_field_is_refused(root: Path) -> None:
    change(root)
    assert incident.main([*args(), "date=2020-01-01"]) == 1
    assert incident.main([*args(), "colour=red"]) == 1


def test_usage_exits_2(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert incident.main([]) == 2
    assert capsys.readouterr().err.startswith("usage: dl incident")


def test_incidents_are_counted_as_their_own_class_and_in_trend(root: Path) -> None:
    for name in ("a", "b"):
        change(root, name)
        assert incident.main(args(name)) == 0
    assert incident.main(args("a", what="a wrong target", root_cause="the default target")) == 0
    counts = {name: (c, total, g) for c, total, name, g in lesson_counts(root)}
    assert counts["incident: no disk check"] == (2, 2, "tests/g.py")
    assert counts["incident: the default target"] == (1, 1, "tests/g.py")
    table = trend(root)
    week = iso_week(datetime.now(timezone.utc))  # noqa: UP017 - 3.10 hosts
    assert table[-2:] == [incident.TREND_HEADER, f"| {week} | 3 | 2 |"], table


def test_no_incident_leaves_trend_as_it_was(root: Path) -> None:
    change(root)
    assert trend(root) == trend(root)[:1]


def test_written_files_are_neutral() -> None:
    here = Path(__file__)
    assert test_neutral.check([here, here.parents[1] / "src/dev_loop/records/incident.py"]) == []


def test_incident_says_it_is_not_committed(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    change(root)
    assert incident.main(args()) == 0
    assert "not committed" in capsys.readouterr().err


def test_the_same_incident_on_another_day_is_recorded_again(root: Path) -> None:
    folder = change(root)
    assert incident.main(args()) == 0
    for name in ("records.jsonl", "incidents.md"):  # the first one happened on an earlier day
        path = folder / name
        path.write_text(path.read_text().replace(
            datetime.now(timezone.utc).strftime("%Y-%m-%d"), "2020-01-01"))  # noqa: UP017
    assert incident.main(args()) == 0
    dates = [json.loads(x)["date"] for x in (folder / "records.jsonl").read_text().splitlines()]
    assert dates[0] == "2020-01-01"
    assert len(dates) == 2
    assert incident.main(args()) == 0  # the same day again is still written once
    assert len((folder / "records.jsonl").read_text().splitlines()) == 2
