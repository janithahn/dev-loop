"""Flake records from JUnit XML (FL1): a flake is recorded, the build stays red, the trend counts.

A testcase that failed and then passed — a rerun element or a second entry with the same id — is a
flake; a rerun never turns a failed build green. The fixtures are built here, so no real test runner
and no client, project or person name is needed.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from test_neutral import check

from dev_loop.records import flakes, review_records

STAMP = "2026-11-17T10:00:00"
WANT = {"kind": "flake", "test": "tests.test_mod::test_a", "file": "tests/test_mod.py"}


def case(name: str, *, fail: bool = False, skip: bool = False, reruns: int = 0) -> str:
    body = "<rerunFailure>boom</rerunFailure>" * reruns
    body += "<failure>boom</failure>" if fail else ""
    body += "<skipped/>" if skip else ""
    return (f'<testcase classname="tests.test_mod" name="{name}" '
            f'file="tests/test_mod.py">{body}</testcase>')


def junit(tmp_path: Path, cases: str, stamp: str = STAMP) -> list[str]:
    text = f'<testsuites><testsuite name="s" timestamp="{stamp}">{cases}</testsuite></testsuites>'
    (tmp_path / "j.xml").write_text(text, encoding="utf-8")
    return [str(tmp_path / "j.xml")]


def put(root: Path, *logged: dict[str, str]) -> None:
    folder = flakes.folder(root)
    folder.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"kind": "flake", **flake}) for flake in logged]
    (folder / flakes.LOG).write_text("\n".join(lines) + "\n", encoding="utf-8")


def week(iso: str) -> str:
    year, number, _ = date.fromisoformat(iso).isocalendar()
    return f"{year}-W{number:02d}"


def test_a_clean_pass_is_green_and_logs_nothing(root: Path, tmp_path: Path) -> None:
    assert flakes.run(root, junit(tmp_path, case("test_a")), None, fail_on_flake=True) == 0
    assert flakes.read(root) == []


def test_a_plain_failure_is_red_without_a_flake(root: Path, tmp_path: Path) -> None:
    files = junit(tmp_path, case("test_a", fail=True))
    assert flakes.run(root, files, None, fail_on_flake=True) == 1
    assert flakes.read(root) == []


def test_a_rerun_flake_is_logged_and_keeps_the_build_red(root: Path, tmp_path: Path) -> None:
    files = junit(tmp_path, case("test_a", reruns=2))
    assert flakes.run(root, files, "b7", fail_on_flake=True) == 1
    assert flakes.read(root) == [WANT | {"date": "2026-11-17", "build": "b7"}]


def test_a_duplicate_testcase_fail_then_pass_is_a_flake(root: Path, tmp_path: Path) -> None:
    files = junit(tmp_path, case("test_a", fail=True) + case("test_a"))
    assert flakes.run(root, files, None, fail_on_flake=True) == 1
    assert [flake["test"] for flake in flakes.read(root)] == ["tests.test_mod::test_a"]


def test_a_final_failure_after_a_rerun_is_red_not_a_flake(root: Path, tmp_path: Path) -> None:
    files = junit(tmp_path, case("test_a", reruns=1, fail=True))
    assert flakes.run(root, files, None, fail_on_flake=True) == 1
    assert flakes.read(root) == []


def test_no_fail_on_flake_logs_the_flake_but_stays_green(root: Path, tmp_path: Path) -> None:
    files = junit(tmp_path, case("test_a", reruns=1))
    assert flakes.run(root, files, None, fail_on_flake=False) == 0
    assert len(flakes.read(root)) == 1


def test_a_skip_is_neither_a_pass_nor_a_fail(root: Path, tmp_path: Path) -> None:
    files = junit(tmp_path, case("test_a", skip=True) + case("test_b", fail=True))
    assert flakes.run(root, files, None, fail_on_flake=True) == 1  # test_b, not the skip
    assert flakes.read(root) == []


def test_running_twice_on_the_same_report_does_not_double_count(
    root: Path, tmp_path: Path
) -> None:
    files = junit(tmp_path, case("test_a", reruns=1))
    flakes.run(root, files, "b7", fail_on_flake=True)
    flakes.run(root, files, "b7", fail_on_flake=True)
    assert len(flakes.read(root)) == 1


def test_a_missing_timestamp_dates_the_flake_today(root: Path, tmp_path: Path) -> None:
    files = junit(tmp_path, case("test_a", reruns=1), stamp="")
    flakes.run(root, files, None, fail_on_flake=True)
    assert flakes.read(root)[0]["date"] == date.today().isoformat()


def test_trend_counts_per_week_and_lists_the_top_five(root: Path) -> None:
    put(
        root,
        {"test": "m::t1", "file": "f", "build": "b", "date": "2026-11-17"},
        {"test": "m::t1", "file": "f", "build": "b", "date": "2026-11-16"},
        {"test": "m::t2", "file": "f", "build": "b", "date": "2026-11-17"},
        {"test": "m::t1", "file": "f", "build": "b", "date": "2026-11-09"},
    )
    lines = flakes.trend(root)
    newest, older = f"| {week('2026-11-17')} | 3 | 2 |", f"| {week('2026-11-09')} | 1 | 1 |"
    assert newest in lines and older in lines
    assert lines.index(newest) < lines.index(older)  # newest week first
    top = lines.index(flakes.TOP_HEADER)
    assert lines[top + 1] == "| m::t1 | 3 |" and lines[top + 2] == "| m::t2 | 1 |"


def test_trend_is_empty_without_flakes(root: Path) -> None:
    assert flakes.trend(root) == []


def test_records_trend_appends_the_flake_tables(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    put(root, {"test": "m::t1", "file": "f", "build": "b", "date": "2026-11-17"})
    assert review_records.main(["trend"]) == 0
    out = capsys.readouterr().out
    assert flakes.WEEK_HEADER.splitlines()[0] in out and "| m::t1 | 1 |" in out


def test_dl_ci_flakes_wires_the_command_and_its_flags(root: Path, tmp_path: Path) -> None:
    from dev_loop.ci import steps

    files = junit(tmp_path, case("test_a", reruns=1))
    assert steps.main(["flakes", "--junit", *files]) == 1
    assert len(flakes.read(root)) == 1
    assert steps.main(["flakes", "--junit", *files, "--no-fail-on-flake"]) == 0


def test_dl_ci_flakes_needs_a_report(root: Path) -> None:
    from dev_loop.ci import steps

    with pytest.raises(SystemExit):
        steps.main(["flakes"])


def test_the_new_files_are_neutral() -> None:
    assert check([
        "plugins/dev-loop/src/dev_loop/records/flakes.py",
        "plugins/dev-loop/tests/test_flakes.py",
    ]) == []
