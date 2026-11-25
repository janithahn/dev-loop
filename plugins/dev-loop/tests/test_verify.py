"""`dl verify`: pass, fail and not run are told apart, the section and the record are written."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest
import yaml
from dev_loop import land, verify
from test_neutral import check

PY = f'"{sys.executable}"'
ROOT = Path(__file__).resolve().parents[3]


def project(root: Path, steps: dict[str, str]) -> Path:
    cfg = {"version": 1, "verify": {"runner": PY, "steps": steps}}
    (root / "dev-loop.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    folder = root / ".dev-loop" / "changes" / "c1"
    folder.mkdir(parents=True)
    (folder / "verify-report.md").write_text("# Verification report: c1\n", encoding="utf-8")
    return folder


def run(folder: Path, *args: str) -> tuple[int, str, list[dict]]:
    code = verify.main(["c1", *args])
    lines = (folder / "records.jsonl").read_text(encoding="utf-8").splitlines()
    return code, (folder / "verify-report.md").read_text(encoding="utf-8"), [
        json.loads(x) for x in lines]


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))


def test_all_pass_is_green(tmp_path: Path) -> None:
    folder = project(tmp_path, {"unit": "-c pass"})
    code, report, records = run(folder)
    assert code == 0
    assert "| unit | passed |" in report
    assert records[-1]["kind"] == "verify" and records[-1]["result"] == "green"


def test_fail_is_red_with_output_tail(tmp_path: Path) -> None:
    folder = project(tmp_path, {"unit": "-c \"print('boom'); raise SystemExit(3)\""})
    code, report, records = run(folder)
    assert code == 1
    assert "| unit | failed |" in report and "boom" in report
    assert records[-1]["result"] == "red"


def test_no_command_and_skipped_are_not_run_never_passed(tmp_path: Path) -> None:
    folder = project(tmp_path, {"lint": "-c pass", "unit": "-c pass", "types": "-c pass"})
    code, report, records = run(folder, "--skip", "types", "--skip", "lint")
    assert "| lint | not run |" in report and "| types | not run |" in report
    assert "| unit | passed |" in report
    assert code == 1 and records[-1]["result"] == "red"


def test_no_steps_is_red(tmp_path: Path) -> None:
    folder = project(tmp_path, {})
    code, _, records = run(folder)
    assert code == 1 and records[-1]["result"] == "red"


def test_a_step_past_its_timeout_is_killed_and_failed(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A hung step never hangs verify, and is never read as passed."""
    folder = project(tmp_path, {"slow": "-c \"import time; time.sleep(30)\""})
    cfg = yaml.safe_load((tmp_path / "dev-loop.yaml").read_text(encoding="utf-8"))
    cfg["verify"]["step_timeout_minutes"] = 1
    (tmp_path / "dev-loop.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    monkeypatch.setattr(verify, "MINUTE", 0.2)  # one "minute" is 200 ms here
    code, report, records = run(folder)
    assert code == 1 and records[-1]["result"] == "red"
    assert "| slow | failed |" in report
    assert "`slow`:\n```\ntimed out after 1 minute(s)\n```" in report


def test_a_timed_out_step_kills_its_children(tmp_path: Path) -> None:
    """The shell's children die with it, so none holds the output open past the timeout."""
    started = time.monotonic()
    sleeper = "import time; time.sleep(30)"
    spawn = f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', '{sleeper}']); "
    command = f'"{sys.executable}" -c "{spawn}time.sleep(30)"'
    outcome, tail = verify.run_step(tmp_path, "", command, 0.01)
    assert outcome == "failed" and tail == "timed out after 0.01 minute(s)"
    assert time.monotonic() - started < 10


def test_kill_uses_taskkill_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(verify.sys, "platform", "win32")
    monkeypatch.setattr(verify.subprocess, "run", lambda argv, **_: calls.append(argv))
    verify._kill(types.SimpleNamespace(pid=42))  # type: ignore[arg-type]
    assert calls == [["taskkill", "/F", "/T", "/PID", "42"]]


def test_files_are_neutral() -> None:
    files = ["plugins/dev-loop/src/dev_loop/verify.py", "plugins/dev-loop/tests/test_verify.py",
             "plugins/dev-loop/skills/verify/SKILL.md"]
    assert check(files, root=ROOT) == []


STEP = "-c \"import sys; open('seen', 'w').write(' '.join(sys.argv[1:]))\" {affected}"


def affected_project(root: Path, affected: str | None) -> Path:
    folder = project(root, {"unit": STEP})
    cfg = yaml.safe_load((root / "dev-loop.yaml").read_text(encoding="utf-8"))
    cfg["verify"]["affected"] = affected
    (root / "dev-loop.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return folder


@pytest.mark.parametrize("affected,seen", [
    ("-c \"print('t1.py t2.py')\"", "t1.py t2.py"),  # the affected tests only, through the runner
    (None, ""),  # not set: the step runs in full
    # a failed lookup runs in full, never on the part it printed before it failed
    ("-c \"print('t1.py'); raise SystemExit(2)\"", ""),
    ("-c pass", ""),  # an empty answer runs in full, never skips
])
def test_verify_affected_fills_the_step_placeholder(
    tmp_path: Path, affected: str | None, seen: str,
) -> None:
    folder = affected_project(tmp_path, affected)
    code, _, _ = run(folder)
    assert code == 0
    assert (tmp_path / "seen").read_text(encoding="utf-8") == seen


def test_an_affected_lookup_past_the_step_timeout_is_killed_and_the_step_runs_in_full(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = affected_project(tmp_path, f"{PY} -c \"import time; time.sleep(30); print('t1.py')\"")
    cfg = yaml.safe_load((tmp_path / "dev-loop.yaml").read_text(encoding="utf-8"))
    cfg["verify"].update(runner="", steps={"unit": f"{PY} {STEP}"}, step_timeout_minutes=1)
    (tmp_path / "dev-loop.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    monkeypatch.setattr(verify, "MINUTE", 3.0)  # one "minute" is three seconds here
    started = time.monotonic()
    code, _, _ = run(folder)
    assert code == 0
    assert (tmp_path / "seen").read_text(encoding="utf-8") == ""
    assert time.monotonic() - started < 20


def test_a_narrowed_step_says_so_in_the_report(tmp_path: Path) -> None:
    folder = affected_project(tmp_path, "-c \"print('t1.py t2.py')\"")
    code, report, records = run(folder)
    assert code == 0 and records[-1]["result"] == "green"
    assert "| unit | passed (affected: 2) |" in report


def test_a_step_run_in_full_carries_no_affected_note(tmp_path: Path) -> None:
    folder = affected_project(tmp_path, "-c pass")
    _, report, _ = run(folder)
    assert "| unit | passed |" in report


def test_land_gates_run_every_step_in_full(tmp_path: Path) -> None:
    affected_project(tmp_path, "-c \"print('t1.py')\"")
    land._gates(tmp_path)  # noqa: SLF001 - the gate land runs before it merges
    assert (tmp_path / "seen").read_text(encoding="utf-8") == ""


def test_a_host_step_runs_without_the_runner(tmp_path: Path) -> None:
    folder = project(tmp_path, {})
    cfg = {"version": 1, "verify": {"runner": "echo RUNNER", "host_steps": ["web"],
                                    "steps": {"web": "echo hostrun", "api": "echo apirun"}}}
    (tmp_path / "dev-loop.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    _, report, _ = run(folder)
    assert "RUNNER echo apirun" in report  # a non-host step keeps the runner
    assert "hostrun" in report and "RUNNER echo hostrun" not in report


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def changed(root: Path, steps: dict[str, str], covers: dict[str, str], path: str,
            production: tuple[str, ...] = ("**",)) -> Path:
    """A project whose one commit past origin/main touches `path`, its steps covering `covers`."""
    folder = project(root, steps)
    cfg = yaml.safe_load((root / "dev-loop.yaml").read_text(encoding="utf-8"))
    cfg["verify"]["covers"] = covers
    cfg["size"] = {"production": list(production)}
    (root / "dev-loop.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    git(root.parent, "init", "-q", "--bare", f"{root.name}-origin.git")
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "noreply@example.invalid")
    git(root, "config", "user.name", "t")
    git(root, "add", "dev-loop.yaml")
    git(root, "commit", "-qm", "base")
    git(root, "remote", "add", "origin", str(root.parent / f"{root.name}-origin.git"))
    git(root, "push", "-q", "origin", "main")
    (root / path).parent.mkdir(parents=True, exist_ok=True)
    (root / path).write_text("x\n", encoding="utf-8")
    git(root, "add", path)
    git(root, "commit", "-qm", "work")
    return folder


def test_a_change_no_step_covers_is_red_naming_the_path(tmp_path: Path) -> None:
    folder = changed(tmp_path, {"lint": "-c pass"}, {"lint": "backend/**"}, "frontend/a.ts")
    code, report, records = run(folder)
    assert code == 1 and records[-1]["result"] == "red"
    assert "| coverage | not run: no step covers frontend/a.ts |" in report


def test_a_change_every_step_covers_is_green(tmp_path: Path) -> None:
    covers = {"lint": "backend/**", "web": "frontend/**"}
    folder = changed(tmp_path, {"lint": "-c pass", "web": "-c pass"}, covers, "frontend/a.ts")
    code, report, records = run(folder)
    assert code == 0 and records[-1]["result"] == "green"
    assert "| coverage | passed |" in report


def test_a_change_to_no_production_path_needs_no_step(tmp_path: Path) -> None:
    folder = changed(tmp_path, {"lint": "-c pass"}, {"lint": "backend/**"}, "docs/x.md",
                     ("src/**", "frontend/**"))
    code, report, _ = run(folder)
    assert code == 0 and "| coverage | passed |" in report


def test_no_covers_writes_no_coverage_row(tmp_path: Path) -> None:
    folder = project(tmp_path, {"unit": "-c pass"})
    code, report, _ = run(folder)
    assert code == 0 and "coverage" not in report


def test_a_failed_step_keeps_its_last_sixty_lines(tmp_path: Path) -> None:
    lines = "-c \"import sys; [print('line', i) for i in range(1, 71)]; sys.exit(1)\""
    folder = project(tmp_path, {"unit": lines})
    _, report, _ = run(folder)
    assert "line 11\n" in report and "line 70\n" in report
    assert "line 10\n" not in report


def test_a_held_record_lock_writes_no_section(tmp_path: Path) -> None:
    folder = project(tmp_path, {"unit": "-c pass"})
    (folder / ".record.lock").write_text(f"{os.getpid()} held", encoding="utf-8")
    before = (folder / "verify-report.md").read_text(encoding="utf-8")
    assert verify.main(["c1"]) == 1
    assert (folder / "verify-report.md").read_text(encoding="utf-8") == before


def test_covers_with_no_production_globs_is_not_run_never_green(tmp_path: Path) -> None:
    folder = changed(tmp_path, {"lint": "-c pass"}, {"lint": "backend/**"}, "frontend/a.ts", ())
    code, report, records = run(folder)
    assert code == 1 and records[-1]["result"] == "red"
    assert "| coverage | not run: size.production is empty" in report


def test_covers_with_no_integration_ref_is_red_naming_the_merge_base(tmp_path: Path) -> None:
    folder = project(tmp_path, {"lint": "-c pass"})
    cfg = yaml.safe_load((tmp_path / "dev-loop.yaml").read_text(encoding="utf-8"))
    cfg["verify"]["covers"] = {"lint": "backend/**"}
    cfg["size"] = {"production": ["**"]}
    (tmp_path / "dev-loop.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    git(tmp_path, "init", "-q", "-b", "main")
    code, report, records = run(folder)
    assert code == 1 and records[-1]["result"] == "red"
    assert "| coverage | not run: no merge base with origin/main |" in report


def test_a_refused_record_puts_the_report_back(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = project(tmp_path, {"unit": "-c pass"})
    before = (folder / "verify-report.md").read_text(encoding="utf-8")

    def refuse(*_: object) -> None:
        raise ValueError("refused")

    monkeypatch.setattr(verify, "write", refuse)
    assert verify.main(["c1"]) == 1
    assert (folder / "verify-report.md").read_text(encoding="utf-8") == before
