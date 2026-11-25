"""`dl verify <change> [--part NAME] [--skip STEP ...]`: run the project's verify steps.

Each `verify.steps` entry is a command run after `verify.runner` (none for a
`verify.host_steps` name) from the project root. A step
with no command, or named by --skip, is "not run", never passed. A step running past
`verify.step_timeout_minutes` is killed with its children and failed, naming the timeout. The
section goes to the change's `verify-report.md`; the result is recorded as green only when
every step passed. `verify.affected`, a command printing the tests a change affects (run after
`verify.runner` under the step timeout, as a step is), fills a `{affected}` placeholder in a step;
an unset, failed, timed-out or empty answer fills it with nothing, so the step runs in full, never
less. A step without the placeholder always runs in full. A step that ran narrowed says so in
the report, `passed (affected: N)`; `dl land` gates on every step in full, never on a narrowed run.

`verify.covers` maps a step to the globs it checks. With it set, every path the change touches
since its merge base with <remote>/<integration> (a part stacked by `dl pr --base`: its
remembered parent, as the size cap judges it) that `size.production` counts (no `size.exclude`
or `size.generated` glob) must match a `covers` glob, else a `coverage` row is "not run" and the
run red; a git that cannot diff, or an empty `size.production`, is red too (nothing compared is
never passed). With `covers` empty, coverage is not checked. The
section is written before the record and put back when the record is refused, so neither is left
without the other.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
from datetime import date
from pathlib import Path

from .ci.additive import git, matches
from .ci.size import stack_base
from .config import Loaded, project_root, required
from .records.record import changes_dir, text_of, write

TAIL, FAIL_TAIL = 8, 60  # lines of a passed, a failed step's output kept in the report
MINUTE = 60.0  # seconds in a minute of verify.step_timeout_minutes


def _kill(step: subprocess.Popen[str]) -> None:
    """Kill the shell and every child it started."""
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(step.pid)],
                       capture_output=True, check=False)
    else:
        os.killpg(step.pid, signal.SIGKILL)


def _run(root: Path, line: str, minutes: float,
         stderr: int = subprocess.STDOUT) -> tuple[int | None, str]:
    """Run one shell line; (exit code, output), the code None when killed past its minutes."""
    with subprocess.Popen(line, shell=True, cwd=root, text=True, stdout=subprocess.PIPE,
                          stderr=stderr, errors="replace", start_new_session=True) as step:
        try:
            output, _ = step.communicate(timeout=minutes * MINUTE)
        except subprocess.TimeoutExpired:
            _kill(step)
            step.communicate()
            return None, ""
    return step.returncode, output


def run_step(root: Path, runner: str, command: str, minutes: float) -> tuple[str, str]:
    """Return (outcome, last output lines) of one command; past its minutes it is failed."""
    code, output = _run(root, f"{runner} {command}".strip(), minutes)
    if code is None:
        return "failed", f"timed out after {minutes:g} minute(s)"
    tail = "\n".join(output.strip().splitlines()[-(TAIL if code == 0 else FAIL_TAIL):])
    return ("passed" if code == 0 else "failed"), tail


def affected_tests(root: Path, runner: str, command: str | None, minutes: float) -> str:
    """The affected command's output on one line; empty when unset, failed, timed out or silent."""
    if not command:
        return ""
    code, output = _run(root, f"{runner} {command}".strip(), minutes, subprocess.DEVNULL)
    return " ".join(output.split()) if code == 0 else ""


def run_all(root: Path, skip: set[str], *, full: bool = False) -> list[tuple[str, str, str]]:
    """Run each step; `full` leaves `{affected}` empty, so no step is narrowed (a gate)."""
    cfg = required(root)
    runner, steps = cfg.get("verify.runner") or "", cfg.get("verify.steps") or {}
    host = cfg.get("verify.host_steps") or []
    minutes = cfg.get("verify.step_timeout_minutes")
    affected = "" if full else affected_tests(root, runner, cfg.get("verify.affected"), minutes)
    out = []
    for name, command in steps.items():
        if name in skip or not command:
            out.append((name, "not run", "no command" if not command else "skipped"))
            continue
        line = str(command).replace("{affected}", affected)
        outcome, tail = run_step(root, "" if name in host else runner, line, minutes)
        if affected and line != str(command):  # narrowed: the outcome says to how many tests
            outcome += f" (affected: {len(affected.split())})"
        out.append((name, outcome, tail))
    return out


def coverage(root: Path, cfg: Loaded) -> list[tuple[str, str, str]]:
    """The `coverage` row: every changed production path a `covers` glob matches; none unset."""
    covers = cfg.get("verify.covers") or {}
    production = cfg.get("size.production") or []
    if not covers:
        return []
    if not production:  # nothing compared is never passed
        return [("coverage", "not run: size.production is empty, nothing to compare", "")]
    ref = stack_base(root) or f"{cfg.get('branches.remote')}/{cfg.get('branches.integration')}"
    base = git(root, "merge-base", ref, "HEAD")
    if base is None:
        return [("coverage", f"not run: no merge base with {ref}", "")]
    paths = git(root, "diff", "--name-only", "--no-renames", base.strip(), "HEAD")
    if paths is None:
        return [("coverage", "not run: git diff failed", "")]
    skip = (cfg.get("size.exclude") or []) + (cfg.get("size.generated") or [])
    globs = [glob for text in covers.values() for glob in str(text).split()]
    missed = [path for path in paths.splitlines() if matches(path, production)
              and not matches(path, skip) and not matches(path, globs)]
    if not missed:
        return [("coverage", "passed", "")]
    more = f" (+{len(missed) - 1} more)" if len(missed) > 1 else ""
    return [("coverage", f"not run: no step covers {missed[0]}{more}", "")]


def section(part: str, results: list[tuple[str, str, str]]) -> str:
    rows = "\n".join(f"| {name} | {outcome} |" for name, outcome, _ in results)
    tails = "\n".join(f"\n`{name}`:\n```\n{tail}\n```" for name, _, tail in results if tail)
    return (f"\n## {part}\n\n**Date:** {date.today().isoformat()}\n\n| Step | Result |\n"
            f"| --- | --- |\n{rows}\n{tails}\n")


def head(root: Path) -> str:
    done = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                          text=True, check=False)
    return done.stdout.strip() if done.returncode == 0 and done.stdout.strip() else "unknown"


def publish(root: Path, report: Path, added: str, record: dict[str, str]) -> bool:
    """Write the section, then the record; a refused record puts the report back as it was."""
    before = text_of(report) if report.is_file() else None
    try:
        report.write_text((before or "") + added, encoding="utf-8")
    except OSError as error:
        print(f"dl verify: cannot write {report}: {error}", file=sys.stderr)
        return False
    try:
        write(root, report.parent.name, record)
    except (TimeoutError, ValueError) as error:
        if before is None:
            report.unlink()
        else:
            report.write_text(before, encoding="utf-8")
        lock = changes_dir(root) / ".record.lock"
        held = isinstance(error, TimeoutError)
        print(f"dl verify: refused: {f'the record lock is held ({lock})' if held else error}",
              file=sys.stderr)
        return False
    return True


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="dl verify")
    parser.add_argument("change")
    parser.add_argument("--part", default="Verify")
    parser.add_argument("--skip", action="append", default=[])
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 2
    root = project_root()
    cfg = required(root)
    report = changes_dir(root) / args.change / "verify-report.md"
    if not report.parent.is_dir():
        print(f"dl verify: no change {args.change!r}", file=sys.stderr)
        return 1
    results = run_all(root, set(args.skip))
    results += coverage(root, cfg) if results else []
    green = bool(results) and all(outcome.startswith("passed") for _, outcome, _ in results)
    record = {"kind": "verify", "result": "green" if green else "red", "head": head(root)}
    if not publish(root, report, section(args.part, results), record):
        return 1
    for name, outcome, _ in results:
        print(f"{outcome:8} {name}")  # a narrowed step: "passed (affected: N)"
    print("verify: green" if green else "verify: red (a step failed, did not run, or none set)")
    return 0 if green else 1
