"""Acceptance batch 11 (Workers and runner, with failures injected): each scriptable row, as a user.

The runner (`worker.sh`) runs in a throwaway HOME, config dir and git config (harness.py) against a
bare repo as the hub (F-HUB), with `dl` and `claude` as stubs first on PATH (F-STUB: STUB_MODE
ok / sleep / hang / exit1 / empty / handoff). Every shell step has a timeout. A row that fails on
today's code is a strict xfail naming its AT id, so the suite stays green and a fix flips it.
Rows the shipped unit tests already pin are not repeated here.

Run: python -m pytest plugins/dev-loop/tests/acceptance -q
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from dev_loop import doctor
from dev_loop.tasks import worker
from harness import Run, f_hub, f_local

DL = """#!/usr/bin/env bash
case "$2" in
  comment) echo "$3 ${*:4}" >>"$HOME/comments" ;;
  show) [ -n "${ROLE-}" ] && echo "Role: $ROLE" ;;
  watch) n=$(cat "$HOME/watches" 2>/dev/null || echo 0); echo $((n + 1)) >"$HOME/watches"
         date -r "$HOME/.dev-loop/worker-run/.heartbeat" +%s.%N >>"$HOME/beats"
         [ "$n" -lt "${WATCH_FAILS:-0}" ] && exit 3
         [ -n "${FEED-}" ] && printf '%s\\n' "$FEED"; exit 0 ;;
  progress) echo "${PROGRESS:-Done 0}" ;;
esac
"""
CLAUDE = """#!/usr/bin/env bash
echo "$DEV_LOOP_TASK $(date +%s.%N) start $DEV_LOOP_ROLE" >>"$HOME/runs"
echo $$ >"$HOME/claude.pid"
head -1 <<<"${@: -1}" >>"$HOME/prompts"
case "${STUB_MODE:-ok}" in
  sleep) sleep "${NAP:-5}" ;;
  hang) sleep 300 & echo $! >"$HOME/child.pid"; sleep 300 ;;
  exit1) exit 1 ;;
  handoff) [ -e "$HOME/handed" ] || { touch "$HOME/handed"; echo done=T1 >"$DEV_LOOP_HANDOVER"; } ;;
esac
echo "$DEV_LOOP_TASK $(date +%s.%N) end" >>"$HOME/runs"
"""


@pytest.fixture
def user(tmp_path: Path) -> Run:
    """F-HUB plus F-STUB: an isolated user with a hub clone at base/proj and the stubs on PATH."""
    run = Run(tmp_path)
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for name, text in (("dl", DL), ("claude", CLAUDE)):
        (stubs / name).write_text(text, encoding="utf-8")
        (stubs / name).chmod(0o755)
    return run


def _env(user: Run, checkout: Path, **extra: str) -> dict[str, str]:
    return user.env | {
        "PATH": f"{user.base / 'stubs'}{os.pathsep}{user.env['PATH']}",
        "DEV_LOOP_WORKER": "w1", "DEV_LOOP_CHECKOUT": str(checkout),
        "DEV_LOOP_WORKTREES": str(user.base / "wt"), "DEV_LOOP_BASE": "main",
        "DEV_LOOP_NEVER": "keep/*", "FEED": "#5", "WORKER_ONCE": "1", "WAIT": "0.1",
        "SLOTS": "3", "POLL_SECONDS": "0.2", "TIMEOUT_HOURS": "1", "DOCKER_SLOTS": "1",
        "DEFAULT_BASE": "main", "DEFAULT_ROLE": "builder", "STALE_HOURS": "24",
    } | extra


def _runner(user: Run, checkout: Path, **extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(worker.SCRIPT)], env=_env(user, checkout, **extra),
                          capture_output=True, text=True, timeout=90, check=False)


def _start(user: Run, checkout: Path, **extra: str) -> subprocess.Popen[str]:
    return subprocess.Popen(["timeout", "90", "bash", str(worker.SCRIPT)], text=True,
                            env=_env(user, checkout, **extra), start_new_session=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _lines(user: Run, name: str) -> list[str]:
    path = user.base / "home" / name
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def _log(user: Run) -> str:
    return "\n".join(_lines(user, ".dev-loop/worker.log"))


def _starts(user: Run) -> list[str]:
    return [line.split()[0] for line in _lines(user, "runs") if line.split()[2] == "start"]


def _alive(pid: int) -> bool:
    """A live process; a zombie counts as dead."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return False
    return stat.rsplit(")", 1)[1].split()[0] != "Z"


def _wait_for(check: Callable[[], object], seconds: float = 30) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end and not check():
        time.sleep(0.1)


# Every refusal of `dl worker` is one line, and no runner starts.
@pytest.fixture
def proj(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "dev-loop.yaml").write_text(
        "version: 1\nbranches: {integration: trunk-x}\nworkers:\n  w1:\n"
        f"    checkout: {tmp_path / 'co'}\n    worktrees: {tmp_path / 'wt'}\n    never: []\n")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DEV_LOOP_WORKER", "w1")
    monkeypatch.delenv("SLOTS", raising=False)
    monkeypatch.setattr(doctor, "rows", lambda _root: [("Python", True, "3.12")])
    return tmp_path


@pytest.mark.parametrize("case", ["no name", "unknown", "red doctor", "otel", "slots"])
def test_w2_each_refusal_is_one_line_and_starts_no_runner(
    proj: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], case: str,
) -> None:
    execs: list[object] = []
    monkeypatch.setattr(worker.os, "execvpe", lambda *a: execs.append(a))
    if case in ("no name", "unknown"):
        monkeypatch.setenv("DEV_LOOP_WORKER", "" if case == "no name" else "nobody")
    elif case == "red doctor":
        monkeypatch.setattr(doctor, "rows", lambda _root: [("credential", False, "absent")])
    elif case == "otel":
        (proj / ".claude").mkdir()
        settings = '{"env": {"OTEL_RESOURCE_ATTRIBUTES": "a"}}'
        (proj / ".claude" / "settings.json").write_text(settings, encoding="utf-8")
    else:
        monkeypatch.setenv("SLOTS", "x")
    assert worker.main([]) != 0
    err = capsys.readouterr().err
    assert len(err.strip().splitlines()) == 1, err
    assert execs == []


def test_w3_two_slots_never_run_more_than_two_and_all_five_finish(user: Run) -> None:
    proj = f_hub(user)
    done = _runner(user, proj, SLOTS="2", FEED="#1 #2 #3 #4 #5", STUB_MODE="sleep", NAP="1")
    assert done.returncode == 0, done.stderr
    events = sorted((float(t), +1 if kind == "start" else -1)
                    for _id, t, kind, *_ in (line.split() for line in _lines(user, "runs")))
    live, most = 0, 0
    for _t, step in events:
        live += step
        most = max(most, live)
    assert most <= 2
    assert sorted(_starts(user)) == ["1", "2", "3", "4", "5"]
    assert len([e for e in events if e[1] < 0]) == 5  # all five finished


def test_w6_a_hung_run_dies_with_its_process_group(user: Run) -> None:
    proj = f_hub(user)  # TIMEOUT_HOURS 0.0008: a 2.9 s timeout
    done = _runner(user, proj, STUB_MODE="hang", TIMEOUT_HOURS="0.0008")
    assert done.returncode == 0, done.stderr
    assert "task #5: exit 124" in _log(user)
    child = int(_lines(user, "child.pid")[0])
    _wait_for(lambda: not _alive(child), 5)
    alive = _alive(child)
    if alive:
        os.kill(child, signal.SIGKILL)
    assert not alive  # no child left
    assert "5 worker w1: run ended with exit 124 (124: the timeout)" in _lines(user, "comments")


def _crash_mid_run(user: Run) -> tuple[Path, int]:
    """Start a runner, kill -9 its whole process group mid-run; return (checkout, claude pid)."""
    proj = f_hub(user)
    first = _start(user, proj, STUB_MODE="sleep", NAP="60", WORKER_ONCE="")
    _wait_for(lambda: _lines(user, "claude.pid"))
    os.killpg(first.pid, signal.SIGKILL)
    first.wait(timeout=10)
    lock = user.base / "home" / ".dev-loop" / "worker-run" / ".runner.lock"
    # a `timeout` child (its own process group) may hold the inherited lock a moment longer
    _wait_for(lambda: subprocess.run(["flock", "-n", str(lock), "true"], timeout=10,
                                     check=False).returncode == 0, 10)
    return proj, int(_lines(user, "claude.pid")[0])


def _kill_session(pid: int) -> None:
    with contextlib.suppress(OSError):
        os.killpg(os.getpgid(pid), signal.SIGKILL)


def test_w7_a_crashed_runners_lock_is_swept_and_the_task_told(user: Run) -> None:
    proj, claude = _crash_mid_run(user)
    _kill_session(claude)
    done = _runner(user, proj, FEED="")  # the restart: one poll
    assert done.returncode == 0, done.stderr
    assert "swept orphaned lock 5" in _log(user)
    assert [c for c in _lines(user, "comments") if c.startswith("5 ") and "without its runner" in c]
    assert not (user.base / "home" / ".dev-loop" / "worker-run" / "5").exists()


@pytest.mark.xfail(strict=True, reason="AT W-7: a crashed runner's run lives on after its sweep")
def test_w7_a_crashed_runners_run_does_not_outlive_its_swept_lock(user: Run) -> None:
    proj, claude = _crash_mid_run(user)
    try:
        assert _runner(user, proj, FEED="").returncode == 0
        assert "swept orphaned lock 5" in _log(user)
        assert not _alive(claude)  # else a re-run of the task runs beside it
    finally:
        _kill_session(claude)


def test_w8_lost_board_and_fetch_are_logged_and_the_runner_recovers(user: Run) -> None:
    proj = f_hub(user)
    user.git(proj, "remote", "set-url", "origin", str(user.base / "gone.git"))
    done = _runner(user, proj, WATCH_FAILS="3", WORKER_ONCE="4", POLL_SECONDS="0.3")
    assert done.returncode == 0, done.stderr  # alive through four polls
    log = _log(user)
    assert log.count("watch failed rc=3") == 3
    assert "fetch failed" in log
    beats = _lines(user, "beats")
    assert len(beats) == 4 and len(set(beats)) == 4  # the heartbeat moved every poll
    assert _starts(user) == ["5"]  # it recovered: the fourth poll ran the task


def test_w11_role_cap_and_hand_off_rerun_opens_with_the_note(user: Run) -> None:
    proj = f_hub(user)
    done = _runner(user, proj, ROLE="planner", STUB_MODE="handoff")
    assert done.returncode == 0, done.stderr
    runs = [line.split() for line in _lines(user, "runs") if " start " in line]
    assert [r[3] for r in runs] == ["planner", "planner"]  # DEV_LOOP_ROLE, a re-run on hand-off
    assert _lines(user, "prompts")[1].startswith("A previous run reached its context cap")
    comments = _lines(user, "comments")
    assert [c for c in comments if "hand-off at the context cap" in c]
    assert not [c for c in comments if "without finishing" in c]  # counted as a hand-off


def _behind(user: Run, proj: Path) -> str:
    """Push a commit from a second clone, so proj's main is one behind its hub; return the tip."""
    other = user.base / "other"
    user.git(user.base, "clone", "-q", str(user.base / "hub.git"), str(other))
    (other / "b.txt").write_text("b\n", encoding="utf-8")
    user.git(other, "add", "b.txt")
    user.git(other, "commit", "-q", "-m", "b")
    user.git(other, "push", "-q", "origin", "main")
    return user.git(other, "rev-parse", "HEAD")


def test_w12_self_update_waits_for_idle(user: Run) -> None:
    proj = f_hub(user)
    tip, behind = _behind(user, proj), user.git(proj, "rev-parse", "HEAD")
    lock = user.base / "home" / ".dev-loop" / "worker-run" / "77"
    lock.mkdir(parents=True)
    busy = subprocess.Popen(["sleep", "60"])
    try:
        (lock / "pid").write_text(str(busy.pid), encoding="utf-8")
        assert _runner(user, proj, FEED="").returncode == 0
        assert user.git(proj, "rev-parse", "HEAD") == behind  # a live run: not idle
    finally:
        busy.kill()
        busy.wait(timeout=10)
    assert _runner(user, proj, FEED="").returncode == 0  # the lock is swept: idle now
    assert user.git(proj, "rev-parse", "HEAD") == tip


def test_w13_the_runner_works_on_a_remote_called_hub(user: Run) -> None:
    proj = f_hub(user, remote="hub")  # F-HUB2
    assert _runner(user, proj).returncode == 0
    assert "fetch failed" not in _log(user)
    assert _starts(user) == ["5"]


def test_w13_tasks_watch_runs_on_the_local_host(user: Run) -> None:
    proj = f_local(user)
    done = user.dl(proj, "tasks", "watch", "--once")
    assert "Traceback" not in done.stderr
    assert done.returncode == 1 and "tasks need host ado or github" in done.stderr
    assert len(done.stderr.strip().splitlines()) == 1  # one line, never a traceback


def test_w14_a_number_in_a_comment_starts_no_run(user: Run) -> None:
    proj = f_hub(user)  # the line `dl tasks watch` prints for a manager comment on task 12
    assert _runner(user, proj, FEED="COORDINATOR COMMENT on #12: see #999").returncode == 0
    assert _starts(user) == ["12"]


def test_w15_the_heartbeat_stays_fresh_while_slots_are_full(user: Run) -> None:
    proj = f_hub(user)
    runner = _start(user, proj, SLOTS="1", FEED="#1 #2", STUB_MODE="sleep", NAP="4",
                    POLL_SECONDS="0.3", WAIT="0.2")
    beat = user.base / "home" / ".dev-loop" / "worker-run" / ".heartbeat"
    stale = 0.0
    while runner.poll() is None and "2" not in _starts(user):  # the slot wait for task 2
        if beat.exists():
            stale = max(stale, time.time() - beat.stat().st_mtime)
        time.sleep(0.25)
    runner.wait(timeout=60)
    assert stale <= 1.5  # five poll intervals


def _worker_starts(proj: Path, monkeypatch: pytest.MonkeyPatch, days: float) -> bool:
    """A gate heartbeat `days` old, then `dl worker` with the doctor's context gate row the only
    row (the others need a host): True when it execs the runner."""
    beat = proj / ".claude" / "dev-loop" / "gate-heartbeat"
    beat.parent.mkdir(parents=True)
    beat.touch()
    os.utime(beat, (time.time() - days * 86400,) * 2)
    execs: list[object] = []
    monkeypatch.setattr(worker.os, "execvpe", lambda *a: execs.append(a))
    monkeypatch.setattr(doctor, "rows", lambda _root: [doctor._context_gate_row()])
    return worker.main([]) == 0 and len(execs) == 1


def test_w16_a_worker_starts_with_a_fresh_gate_heartbeat(
    proj: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _worker_starts(proj, monkeypatch, 1)


def test_w16_a_worker_starts_after_an_idle_week(
    proj: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _worker_starts(proj, monkeypatch, 8)
