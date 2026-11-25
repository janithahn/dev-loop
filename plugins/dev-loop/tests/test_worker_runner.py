"""`worker.sh` runs: per-run telemetry tags, one re-run, one runner per run dir, problems told.

The host CLI (`dl`) and `claude` are stubs on PATH; no network, no host.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from dev_loop.tasks import worker

DL = """#!/usr/bin/env bash
case "$2" in
  comment) echo "$3 ${*:4}" >>"$HOME/comments" ;;
  watch) [ -n "${TASKS_OUT-}" ] && printf '%s\\n' "$TASKS_OUT"; exit "${TASKS_RC:-0}" ;;
  progress) [ -n "${PROGRESS_RC-}" ] && exit "$PROGRESS_RC"
            if [ -n "${PROGRESS_FILE-}" ] && [ -f "$PROGRESS_FILE" ]; then cat "$PROGRESS_FILE"
            else echo "${PROGRESS:-Done 0}"; fi ;;
  sweep) [ -n "${SWEEP_OUT-}" ] && printf '%s\\n' "$SWEEP_OUT"; exit "${SWEEP_RC:-0}" ;;
esac
"""
CLAUDE = """#!/usr/bin/env bash
echo "$DEV_LOOP_TASK ${OTEL_RESOURCE_ATTRIBUTES-unset}" >>"$HOME/runs"
echo "$PWD" >>"$HOME/pwd"
sleep "${NAP:-0.2}"
exit "${CLAUDE_RC:-0}"
"""
# A git stub: 'worktree add' is a cheap mkdir (no real base ref needed); all else is real git.
GIT = """#!/usr/bin/env bash
if [ "$1" = worktree ] && [ "$2" = add ]; then
  for a in "$@"; do p=$t; t=$a; done  # p: the <path> arg, t: the <base> arg (the last two)
  mkdir -p "$p"; exit 0
fi
exec {real} "$@"
"""
TAGS = "host.name=w1,dev_loop.worker=w1,dev_loop.agent=worker,dev_loop.repo=sample-repo"


def _locks(run: Path) -> list[str]:
    """The task lock dirs left in the run dir (the .runner.lock and .heartbeat dotfiles aside)."""
    return sorted(p.name for p in run.iterdir() if not p.name.startswith("."))


@pytest.fixture
def home(tmp_path: Path) -> Path:
    (tmp_path / "bin").mkdir()
    stubs = (("dl", DL), ("claude", CLAUDE), ("git", GIT.format(real=shutil.which("git"))))
    for name, text in stubs:
        (tmp_path / "bin" / name).write_text(text, encoding="utf-8")
        (tmp_path / "bin" / name).chmod(0o755)
    checkout = tmp_path / "co"
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    origin = str(tmp_path / "gone" / "sample-repo.git")
    subprocess.run(["git", "-C", str(checkout), "remote", "add", "origin", origin], check=True)
    return tmp_path


def _env(home: Path, **extra: str) -> dict[str, str]:
    keep = {k: v for k, v in os.environ.items()
            if not k.startswith(("DEV_LOOP_", "OTEL_", "CLAUDE_CODE_", "SLOTS"))}
    return keep | {
        "HOME": str(home), "PATH": f"{home / 'bin'}{os.pathsep}{os.environ['PATH']}",
        "DEV_LOOP_WORKER": "w1", "DEV_LOOP_CHECKOUT": str(home / "co"),
        "DEV_LOOP_WORKTREES": str(home / "wt"), "DEV_LOOP_BASE": "trunk-x",
        "DEV_LOOP_NEVER": "keep/*", "TASKS_OUT": "#5", "WORKER_ONCE": "1", "WAIT": "0.1",
        "SLOTS": "3", "POLL_SECONDS": "0.1", "TIMEOUT_HOURS": "1", "DOCKER_SLOTS": "1",
        "DEFAULT_BASE": "trunk-x", "DEFAULT_ROLE": "builder-backend", "STALE_HOURS": "24",
    } | extra


def _run(home: Path, **extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(worker.SCRIPT)], env=_env(home, **extra),
                          capture_output=True, text=True, timeout=60, check=False)


def _read(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


@pytest.mark.parametrize("switch", ["DEV_LOOP_TELEMETRY", "CLAUDE_CODE_ENABLE_TELEMETRY"])
def test_each_run_carries_its_tags(home: Path, switch: str) -> None:
    assert _run(home, **{switch: "1"}).returncode == 0
    assert _read(home / "runs") == [f"5 {TAGS},dev_loop.task=5"]


def test_no_tags_while_telemetry_is_off(home: Path) -> None:
    assert _run(home, OTEL_RESOURCE_ATTRIBUTES="stale=1").returncode == 0
    assert _read(home / "runs") == ["5 unset"]


def test_an_arrival_during_a_run_queues_one_rerun(home: Path) -> None:
    assert _run(home, NAP="2", WORKER_ONCE="4", POLL_SECONDS="0.3").returncode == 0
    assert _read(home / "runs") == ["5 unset", "5 unset"]
    log = (home / ".dev-loop" / "worker.log").read_text(encoding="utf-8")
    assert "re-run queued" in log
    assert "re-run for" in log
    assert _locks(home / ".dev-loop" / "worker-run") == []


def test_a_second_runner_is_refused(home: Path) -> None:
    first = subprocess.Popen(["bash", str(worker.SCRIPT)], env=_env(home, NAP="3"))
    lock = home / ".dev-loop" / "worker-run" / "5"
    for _ in range(100):
        if lock.exists():
            break
        time.sleep(0.1)
    second = _run(home)
    first.wait(timeout=60)
    assert second.returncode == 1
    assert "another runner holds" in second.stderr
    assert _read(home / "runs") == ["5 unset"]


def test_a_held_marker_with_fd_9_on_another_file_is_refused(home: Path) -> None:
    """v0.4.22 review minor 1: an outside marker plus a foreign fd 9 never pass the lock."""
    run = home / ".dev-loop" / "worker-run"
    run.mkdir(parents=True)
    lock = str(run / ".runner.lock")
    holder = subprocess.Popen(["flock", "-o", lock, "sleep", "30"])  # -o: flock alone holds it
    try:
        for _ in range(100):
            if subprocess.run(["flock", "-n", lock, "true"], check=False).returncode:
                break
            time.sleep(0.05)
        second = subprocess.run(
            ["bash", "-c", f'exec 9>"$HOME/other"; exec bash {worker.SCRIPT}'],
            env=_env(home, DEV_LOOP_RUNNER_LOCK="held"),
            capture_output=True, text=True, timeout=60, check=False)
    finally:
        holder.kill()
        holder.wait()
    assert second.returncode == 1, second.stderr
    assert "another runner holds" in second.stderr
    assert _read(home / "runs") == []


def test_a_failed_run_is_told_on_its_task(home: Path) -> None:
    assert _run(home, CLAUDE_RC="1").returncode == 0
    assert _read(home / "comments") == ["5 worker w1: run ended with exit 1 (124: the timeout)"]


def test_a_swept_lock_is_told_on_its_task(home: Path) -> None:
    orphan = home / ".dev-loop" / "worker-run" / "9"
    orphan.mkdir(parents=True)
    (orphan / "pid").write_text("99999999", encoding="utf-8")
    assert _run(home, TASKS_OUT="").returncode == 0
    assert not orphan.exists()
    assert len([line for line in _read(home / "comments") if line.startswith("9 ")]) == 1


def test_a_failed_watch_is_logged(home: Path) -> None:
    assert _run(home, TASKS_OUT="", TASKS_RC="3").returncode == 0
    assert "watch failed rc=3" in (home / ".dev-loop" / "worker.log").read_text(encoding="utf-8")


def _claude(home: Path, body: str = "") -> None:
    """A run that records itself and does only `body`: it never finishes the task on its own."""
    claude = home / "bin" / "claude"
    claude.write_text(f'#!/usr/bin/env bash\necho run >>"$HOME/runs"\n{body}\nexit 0\n')
    claude.chmod(0o755)


def test_a_stall_is_re_run_once_then_handed_over(home: Path) -> None:
    _claude(home)  # the run does nothing: the task stays Doing with no new [worker] comment
    assert _run(home, PROGRESS="Doing 7").returncode == 0
    assert _read(home / "runs") == ["run", "run"]  # the stall is re-run exactly once
    comments = _read(home / "comments")
    assert [c for c in comments if "run ended without finishing (exit 0)" in c]
    assert any(f"continue from the worktree {home / 'wt'}/5" in c for c in comments)
    assert [c for c in comments if "stalled twice; needs the coordinator" in c]
    assert _locks(home / ".dev-loop" / "worker-run") == []


def test_a_run_that_reaches_done_is_not_re_run(home: Path) -> None:
    _claude(home, f'echo "Done 9" >"{home / "prog"}"')
    assert _run(home, PROGRESS="Doing 0", PROGRESS_FILE=str(home / "prog")).returncode == 0
    assert _read(home / "runs") == ["run"]
    assert not [c for c in _read(home / "comments") if "finishing" in c or "stalled" in c]


def test_a_run_that_comments_is_not_a_stall(home: Path) -> None:
    _claude(home, f'echo "Doing 5" >"{home / "prog"}"')  # its last comment id moved from 2 to 5
    assert _run(home, PROGRESS="Doing 2", PROGRESS_FILE=str(home / "prog")).returncode == 0
    assert _read(home / "runs") == ["run"]


def test_an_unreadable_progress_is_never_read_as_a_healthy_run(home: Path) -> None:
    _claude(home)  # the progress read itself fails, so a stall cannot be ruled out
    assert _run(home, PROGRESS_RC="3").returncode == 0
    assert _read(home / "runs") == ["run"]  # no re-run on an unreadable check
    assert [c for c in _read(home / "comments") if "stall check could not read the task" in c]
    assert _locks(home / ".dev-loop" / "worker-run") == []


def test_each_run_is_capped_at_its_role(home: Path) -> None:
    """Each run exports DEV_LOOP_ROLE — the worker default, or the brief's `Role:` line."""
    claude = home / "bin" / "claude"
    claude.write_text('#!/usr/bin/env bash\necho "$DEV_LOOP_ROLE" >>"$HOME/roles"\nexit 0\n')
    claude.chmod(0o755)
    assert _run(home).returncode == 0
    assert _read(home / "roles") == ["builder-backend"]  # no Role: line → the worker default
    (home / "bin" / "dl").write_text(DL.replace("esac", '  show) echo "Role: reviewer" ;;\nesac'))
    (home / "bin" / "dl").chmod(0o755)
    (home / "roles").unlink()
    assert _run(home).returncode == 0
    assert _read(home / "roles") == ["reviewer"]  # the brief's Role: line wins over the default


def _claude_hand_off(home: Path) -> None:
    """A run that hands off once (writes DEV_LOOP_HANDOVER), then reaches Done on its re-run."""
    claude = home / "bin" / "claude"
    claude.write_text(
        '#!/usr/bin/env bash\necho run >>"$HOME/runs"\n'
        'printf "%s\\n--END--\\n" "${@: -1}" >>"$HOME/prompts"\n'
        'if [ -e "$HOME/handed" ]; then echo "Done 9" >"$HOME/prog"\n'
        'else touch "$HOME/handed"; echo "done=T1; next=T2" >"$DEV_LOOP_HANDOVER"; fi\nexit 0\n')
    claude.chmod(0o755)


def test_a_hand_off_is_re_run_with_the_note_not_a_stall(home: Path) -> None:
    _claude_hand_off(home)  # the first run hands off at its cap; the second finishes the task
    assert _run(home, PROGRESS="Doing 7", PROGRESS_FILE=str(home / "prog")).returncode == 0
    assert _read(home / "runs") == ["run", "run"]  # the hand-off is re-run exactly once
    comments = _read(home / "comments")
    assert [c for c in comments if "hand-off at the context cap" in c]
    assert any("Hand-over note: done=T1; next=T2" in p for p in _read(home / "prompts"))
    assert not [c for c in comments if "without finishing" in c or "stalled twice" in c]
    assert _locks(home / ".dev-loop" / "worker-run") == []


def test_a_heartbeat_is_written_every_poll(home: Path) -> None:
    assert _run(home).returncode == 0
    assert (home / ".dev-loop" / "worker-run" / ".heartbeat").exists()


def test_the_runner_makes_the_worktree_and_the_run_starts_inside_it(home: Path) -> None:
    assert _run(home).returncode == 0
    tree = home / "wt" / "5"
    assert tree.is_dir()  # the runner created it off origin/trunk-x (the git stub mkdir'd it)
    assert _read(home / "pwd") == [str(tree)]  # the run started in the worktree, not the checkout


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def test_a_runs_children_die_with_the_run(home: Path) -> None:
    claude = home / "bin" / "claude"
    claude.write_text('#!/usr/bin/env bash\necho run >>"$HOME/runs"\n'
                      'sleep 30 & echo $! >"$HOME/strag.pid"\nsleep 0.3\nexit 0\n')
    claude.chmod(0o755)
    assert _run(home).returncode == 0
    strag = int((home / "strag.pid").read_text())
    time.sleep(0.4)
    still = _alive(strag)
    if still:  # the feature is missing: do not leak the straggler out of the test
        os.kill(strag, 9)
    assert not still  # the run's process group was killed when the run ended


def test_a_dead_lock_is_swept_during_the_slot_wait(home: Path) -> None:
    run = home / ".dev-loop" / "worker-run"
    run.mkdir(parents=True)
    lock = run / "88"
    lock.mkdir()
    # Orphan the sleep to init (not our child, so no lingering zombie): alive at the first poll,
    # gone during the slot wait, when kill -0 then fails and the lock is swept.
    subprocess.run(["sh", "-c", f'sleep 1.2 & echo $! >"{lock / "pid"}"'], check=True, timeout=10)
    result = _run(home, SLOTS="1", TASKS_OUT="#6", WAIT="0.2")
    assert result.returncode == 0
    assert _read(home / "runs") == ["6 unset"]  # the slot wait was not blocked by the dead lock
    assert "swept orphaned lock 88" in (home / ".dev-loop" / "worker.log").read_text()


def _unpushed_worktree(home: Path, item_id: str) -> None:
    """A worktree with a commit on no remote: unfinished work the stall verdict must catch."""
    tree = home / "wt" / item_id
    tree.mkdir(parents=True)
    run = ["git", "-C", str(tree)]
    subprocess.run([*run, "init", "-q", "-b", "work"], check=True)
    subprocess.run([*run, "config", "user.email", "noreply@example.com"], check=True)
    subprocess.run([*run, "config", "user.name", "t"], check=True)
    (tree / "f.txt").write_text("x")
    subprocess.run([*run, "add", "."], check=True)
    subprocess.run([*run, "commit", "-qm", "wip"], check=True)


def test_a_run_that_comments_but_leaves_unpushed_work_is_a_stall(home: Path) -> None:
    _unpushed_worktree(home, "5")
    _claude(home, f'echo "Doing 9" >"{home / "prog"}"')  # its comment id moves from 2 to 9
    assert _run(home, PROGRESS="Doing 2", PROGRESS_FILE=str(home / "prog")).returncode == 0
    assert _read(home / "runs") == ["run", "run"]  # worktree-aware: re-run despite the comment
    assert [c for c in _read(home / "comments") if "stalled twice; needs the coordinator" in c]


def test_the_poll_runs_the_sweep_and_runs_its_ids(home: Path) -> None:
    assert _run(home, TASKS_OUT="", SWEEP_OUT="#8").returncode == 0
    assert _read(home / "runs") == ["8 unset"]  # a sweep-emitted id joins the feed and runs


def _co(co: Path, *args: str) -> str:
    env = os.environ | {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    return subprocess.run(["git", "-C", str(co), *args], check=True, capture_output=True,
                          text=True, env=env).stdout.strip()


def _behind_checkout(home: Path) -> tuple[Path, str, str]:
    """Rebuild home/co on trunk-x one commit behind a real origin; return (co, behind, tip)."""
    co, origin, other = home / "co", home / "origin.git", home / "other"
    shutil.rmtree(co)
    subprocess.run(["git", "init", "-q", "--bare", "-b", "trunk-x", str(origin)],
                   check=True, capture_output=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(co)], check=True, capture_output=True)
    (co / "a").write_text("a", encoding="utf-8")
    _co(co, "add", "a"); _co(co, "commit", "-q", "-m", "a")
    _co(co, "push", "-q", "origin", "trunk-x")
    behind = _co(co, "rev-parse", "HEAD")
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True, capture_output=True)
    (other / "b").write_text("b", encoding="utf-8")
    _co(other, "add", "b"); _co(other, "commit", "-q", "-m", "b")
    tip = _co(other, "rev-parse", "HEAD"); _co(other, "push", "-q", "origin", "trunk-x")
    return co, behind, tip


def _log(home: Path) -> str:
    return (home / ".dev-loop" / "worker.log").read_text(encoding="utf-8")


def test_an_idle_runner_fast_forwards_to_integration_and_re_execs(home: Path) -> None:
    co, behind, tip = _behind_checkout(home)
    assert _run(home, TASKS_OUT="").returncode == 0
    assert _co(co, "rev-parse", "HEAD") == tip != behind
    assert "self-update: fast-forwarded trunk-x" in _log(home)


def test_the_re_exec_keeps_its_lock_while_a_child_still_holds_it(home: Path) -> None:
    real = shutil.which("git")  # before the wrapper shadows it on PATH
    wrapper = home / "bin" / "git"
    wrapper.write_text("#!/usr/bin/env bash\n"  # a detached child of merge, as git maintenance
                       '[ "$1" = merge ] && { sleep 3 >/dev/null 2>&1 & }\n'
                       f'exec {real} "$@"\n', encoding="utf-8")
    co, behind, tip = _behind_checkout(home)
    done = _run(home, TASKS_OUT="")
    assert done.returncode == 0, done.stderr
    assert _co(co, "rev-parse", "HEAD") == tip != behind
    assert "self-update: fast-forwarded trunk-x" in _log(home)
    assert "another runner holds" not in done.stderr


def test_a_dirty_checkout_is_left_untouched(home: Path) -> None:
    co, behind, _tip = _behind_checkout(home)
    (co / "a").write_text("edited, not committed", encoding="utf-8")
    assert _run(home, TASKS_OUT="").returncode == 0
    assert _co(co, "rev-parse", "HEAD") == behind
    assert "self-update" not in _log(home)


def test_unpushed_work_is_never_dropped(home: Path) -> None:
    co, _behind, tip = _behind_checkout(home)
    (co / "local").write_text("local", encoding="utf-8")
    _co(co, "add", "local"); _co(co, "commit", "-q", "-m", "local")
    mine = _co(co, "rev-parse", "HEAD")
    assert _run(home, TASKS_OUT="").returncode == 0
    assert _co(co, "rev-parse", "HEAD") == mine != tip
    assert "self-update" not in _log(home)


def test_a_run_is_capped_at_max_turns(home: Path) -> None:
    claude = home / "bin" / "claude"
    claude.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@" >>"$HOME/args"\nexit 0\n')
    claude.chmod(0o755)
    assert _run(home, MAX_TURNS="7").returncode == 0
    args = _read(home / "args")
    assert args[args.index("--max-turns") + 1] == "7"
    (home / "args").unlink()
    assert _run(home).returncode == 0
    assert "--max-turns" not in _read(home / "args")
