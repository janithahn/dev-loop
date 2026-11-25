"""Acceptance batch 3, the ci rows, run through `bin/dl` (harness: batch3.py).

A row that fails on today's code is a strict xfail naming its symptom, so a fix flips it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from batch3 import HANG, dl, edit, env, f_hub, git, local, run  # noqa: F401
from test_e2e_local import _finish, _ready, host

BOUND = 100  # seconds: a one-minute step timeout plus slack


def _hang(work: Path) -> None:
    edit(work, {"verify": {"steps": {"unit": HANG}, "step_timeout_minutes": 1,
                           "prepush": ["unit"]}})


def test_d7_verify_cuts_off_a_step_that_never_returns(local: Path, env: dict[str, str]) -> None:
    _hang(local)
    (local / ".dev-loop" / "changes" / "demo").mkdir(parents=True)
    done = dl(local, env, "verify", "demo", timeout=BOUND)
    assert done.code == 1, f"killed after {done.seconds:.0f} s"
    assert "verify: red" in done.out
    report = (local / ".dev-loop" / "changes" / "demo" / "verify-report.md").read_text()
    assert "timed out after 1 minute(s)" in report


def test_d7_pre_push_cuts_off_a_step_that_never_returns(
    local: Path, env: dict[str, str],
) -> None:
    _hang(local)
    git(local, env, "checkout", "-qb", "loop/hang")
    git(local, env, "add", "-A")
    git(local, env, "commit", "-qm", "hang")
    done = run(["git", "push", "origin", "loop/hang"], local, env, BOUND)
    assert done.code not in (None, 0), f"killed after {done.seconds:.0f} s"
    assert "timed out" in done.out


def test_d7_land_cuts_off_a_gate_that_never_returns(tmp_path: Path, env: dict[str, str]) -> None:
    """F-CHG through test_e2e_local's steps, with a gate that never returns."""
    work = f_hub(tmp_path, env)
    tree, _branch, pr = _ready(work, env, HANG)
    _finish(tree, env, pr)
    edit(tree, {"verify": {"step_timeout_minutes": 1}})
    git(tree, env, "commit", "-qam", "step timeout")  # the gate, not a stale round, must stop it
    head = git(tree, env, "rev-parse", "HEAD")
    host(tree, env, "review-run", {"pr": pr, "head": head, "base": "full", "rc": 0})
    host(tree, env, "set-status", {"pr": pr, "sha": head, "state": "success",
                                   "description": "round 1: 0b 0M"})
    done = dl(tree, env, "land", pr, timeout=BOUND)
    assert done.code == 1, f"killed after {done.seconds:.0f} s"
    assert "gate failed: gate" in done.out


def _unpinned(work: Path) -> None:
    (work / ".claude" / "settings.json").unlink()


CANNOT_RUN = [
    pytest.param(["size-cap", "--base", "no-such-ref"], id="size-cap-bad-base"),
    pytest.param(["done-rule", "--base", "no-such-ref"], id="done-rule-bad-base"),
    pytest.param(["pin-check"], id="pin-check-no-pin"),
    pytest.param(["licences"], id="licences-no-manifest"),
    pytest.param(["fix-proves"], id="fix-proves-unconfigured"),
]


@pytest.mark.parametrize("args", CANNOT_RUN)
def test_d8_a_step_that_cannot_run_says_not_run_never_green(
    local: Path, env: dict[str, str], args: list[str],
) -> None:
    _unpinned(local)
    done = dl(local, env, "ci", *args)
    assert "not run" in done.out, done.out
    assert done.code not in (None, 0), done.out


def test_d9_done_rule_on_no_change_says_none_was_judged(local: Path, env: dict[str, str]) -> None:
    git(local, env, "checkout", "-qb", "loop/docs")
    (local / "README.md").write_text("# app, edited\n", encoding="utf-8")
    git(local, env, "commit", "-qam", "docs")
    done = dl(local, env, "ci", "done-rule", "--base", "origin/main")
    assert "no change" in done.out, done.out
    assert done.code not in (None, 0)


def test_d9_done_rule_does_not_skip_a_change_without_tasks(
    local: Path, env: dict[str, str],
) -> None:
    folder = local / ".dev-loop" / "changes" / "demo"
    folder.mkdir(parents=True)
    (folder / "status.md").write_text("# demo\n", encoding="utf-8")
    done = dl(local, env, "ci", "done-rule", "demo")
    assert done.code not in (None, 0), done.out


def test_d11_hooks_and_verdict_selftests(local: Path, env: dict[str, str]) -> None:
    hooks = dl(local, env, "ci", "hooks-selftest")
    assert hooks.code == 0, hooks.out
    codes = [line.split(": exit ")[1].split()[0] for line in hooks.out.splitlines()
             if ": exit " in line]
    assert codes, hooks.out
    assert set(codes) <= {"0", "2"}, hooks.out
    verdict = dl(local, env, "ci", "verdict-selftest")
    assert (verdict.code, verdict.out.strip()) == (0, "verdict contract: green")


# Samples are built at run time so this file stays clean for the scan.
SAMPLES = {
    "github": "GH=" + "ghp_" + "a1B2" * 9,
    "private-key": "-----BEGIN " + "RSA PRIVATE KEY-----",
    "ado-pat": "AZURE_DEVOPS_EXT_PAT=" + "q7" * 26,
    "anthropic": "ANTHROPIC_API_KEY=" + "sk-" + "ant-api03-" + "Zx9_" * 23 + "AA",
}


def _planted(tmp_path: Path, env: dict[str, str], name: str, data: bytes) -> Path:
    work = f_hub(tmp_path, env)
    (work / f"{name}.txt").write_bytes(data)
    git(work, env, "add", "-A")
    git(work, env, "commit", "-qm", "plant")
    return work


@pytest.mark.parametrize("name", [
    "github", "private-key",
    "ado-pat", "anthropic",
])
def test_d12_secret_scan_names_a_planted_token_never_its_value(
    tmp_path: Path, env: dict[str, str], name: str,
) -> None:
    work = _planted(tmp_path, env, name, f"first line\n{SAMPLES[name]}\n".encode())
    done = dl(work, env, "ci", "secret-scan")
    assert done.code == 1, done.out
    assert f"{name}.txt:2:" in done.out
    assert SAMPLES[name].split("=")[-1] not in done.out


def test_d12_secret_scan_says_an_unreadable_file_was_not_scanned(
    tmp_path: Path, env: dict[str, str],
) -> None:
    work = _planted(tmp_path, env, "wide", SAMPLES["github"].encode("utf-16"))
    done = dl(work, env, "ci", "secret-scan")
    assert "wide.txt" in done.out and "not scanned" in done.out, done.out
    assert done.code == 1


def _push(work: Path, env: dict[str, str], prepush: list[str]) -> str:
    edit(work, {"verify": {"prepush": prepush}})
    git(work, env, "add", "-A")
    git(work, env, "commit", "-qm", f"prepush {len(prepush)}")
    done = run(["git", "push", "origin", "loop/push"], work, env, 300)
    assert done.code is not None, done.out
    return f"exit {done.code}\n{done.out}"


def test_d13_pre_push_runs_the_cheap_steps_and_fails_closed(
    local: Path, env: dict[str, str],
) -> None:
    git(local, env, "checkout", "-qb", "loop/push")
    out = _push(local, env, ["unit"])
    assert out.startswith("exit 0"), out
    for step in ("size-cap", "records-current", "rules-current", "unparsed", "unit"):
        assert f"PASS {step} (" in out, out
    out = _push(local, env, ["unit", "missing"])
    assert not out.startswith("exit 0"), out
    assert "FAIL missing (no verify.steps.missing)" in out, out
