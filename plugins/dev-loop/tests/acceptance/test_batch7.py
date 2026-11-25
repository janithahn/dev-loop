"""Acceptance batch 7 (local host end to end): the plan's scriptable rows, run through `bin/dl`.

Each test isolates HOME, CLAUDE_CONFIG_DIR, CLAUDE_PLUGIN_DATA and the git config under its own
temporary folder; a fake `claude` first on PATH logs any call. The hub is a local bare repo.
A row that fails on today's code is a strict xfail naming its symptom, so a fix flips it.
"""

from __future__ import annotations

import fcntl
import json
import os
import signal
import subprocess
from pathlib import Path

import pytest
from batch7_harness import (
    CHANGE,
    DL,
    DONE,
    PLUGIN,
    clean,
    dl,
    edit_config,
    env,
    finish,
    git,
    host,
    hub_main,
    make_chg,
    make_local,
    open_pr,
    ready,
    stopped,
)

__all__ = ["env"]  # a fixture


@pytest.fixture
def local(tmp_path: Path, env: dict[str, str]) -> Path:
    return make_local(tmp_path, env)


@pytest.fixture
def chg(local: Path, env: dict[str, str]) -> Path:
    return make_chg(local, env)


def test_l2_local_land_names_every_missing_done_line(chg: Path, env: dict[str, str]) -> None:
    pr = open_pr(chg, env)
    sidecar = chg / CHANGE / "records.jsonl"  # dl pr needed the simplifier record; drop it here
    rows = [x for x in sidecar.read_text(encoding="utf-8").splitlines()
            if json.loads(x)["kind"] != "simplifier"]
    sidecar.write_text("".join(f"{x}\n" for x in rows), encoding="utf-8")
    finish(chg, env, DONE[:1])  # a clean round only: no verify, lessons, simplifier, skills
    clean(chg, env, pr)
    before = hub_main(env)
    done = dl(chg, env, "land", pr)
    assert stopped(done, "not done: demo: missing", "verify", "Lessons logged", "simplifier",
                   "skills", "metrics row"), done.stdout + done.stderr
    assert hub_main(env) == before


def test_l3_pr_and_review_records_are_committed(chg: Path, env: dict[str, str],
                                                tmp_path: Path) -> None:
    pr = open_pr(chg, env)
    host(chg, env, "thread-post", {"pr": pr, "file": "app.py", "line": 1, "text": "`crid:a1`"})
    assert git(chg, env, "status", "--porcelain") == ""
    tracked = git(chg, env, "ls-files", CHANGE).splitlines()
    assert {f"{CHANGE}/host.jsonl", f"{CHANGE}/review-threads.jsonl"} <= set(tracked)
    branch = git(chg, env, "branch", "--show-current")
    git(chg, env, "push", "-q", "origin", branch)
    other = tmp_path / "other"
    git(tmp_path, env, "clone", "-q", "-b", branch, str(tmp_path / "hub.git"), str(other))
    assert host(other, env, "threads", {"pr": pr})[0]["comments"][0]["content"] == "`crid:a1`"


@pytest.mark.parametrize("case", ["new commit after the clean round", "a later failure"])
def test_l4_land_needs_a_clean_round_for_the_head(case: str, chg: Path,
                                                  env: dict[str, str]) -> None:
    pr = ready(chg, env)
    if case == "a later failure":
        clean(chg, env, pr, "failure")
    else:
        (chg / "app.py").write_text("x = 2\n", encoding="utf-8")
        git(chg, env, "commit", "-qam", "after the round")
    before = hub_main(env)
    done = dl(chg, env, "land", pr)
    assert done.returncode == 1, done.stdout
    assert hub_main(env) == before


@pytest.mark.xfail(strict=True, reason="AT H-L5: land gates the current checkout, not the "
                   "pushed head (main checkout: no pull request)")
@pytest.mark.parametrize("where", ["main checkout", "dirty change worktree"])
def test_l5_land_gates_run_on_the_pr_head(where: str, chg: Path, local: Path,
                                          env: dict[str, str]) -> None:
    pr = ready(chg, env)
    (chg / "unit.sh").write_text("exit 1\n", encoding="utf-8")  # fails on the branch only
    edit_config(chg, lambda c: c["verify"]["steps"].update(unit="sh unit.sh"))
    git(chg, env, "add", "-A")
    git(chg, env, "commit", "-qm", "a failing unit step")
    clean(chg, env, pr)
    if where == "dirty change worktree":
        (chg / "unit.sh").write_text("exit 0\n", encoding="utf-8")  # uncommitted, never pushed
    before = hub_main(env)
    done = dl(local if where == "main checkout" else chg, env, "land", pr)
    assert "gate failed" in done.stdout, done.stdout + done.stderr
    assert hub_main(env) == before


def hold_lock(env: dict[str, str]) -> object:
    path = Path(env["HOME"]) / ".dev-loop" / "land.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return handle


@pytest.mark.parametrize("case", ["un-rebased", "release target", "active thread",
                                  "no verify steps", "lock held"])
def test_l6_land_refusals(case: str, chg: Path, env: dict[str, str], tmp_path: Path) -> None:
    pr, held = ready(chg, env), None
    if case == "un-rebased":
        other = tmp_path / "ahead"
        git(tmp_path, env, "clone", "-q", str(tmp_path / "hub.git"), str(other))
        (other / "b.py").write_text("y = 1\n", encoding="utf-8")
        git(other, env, "add", "-A")
        git(other, env, "commit", "-qm", "ahead")
        git(other, env, "push", "-q", "origin", "HEAD:main")
    if case == "release target":
        edit_config(chg, lambda c: c["branches"].update(release="main"))
    if case == "active thread":
        host(chg, env, "thread-post", {"pr": pr, "text": "**major** x\n\n`crid:b2`"})
    if case == "no verify steps":
        edit_config(chg, lambda c: c["verify"].update(steps={}))
    if case == "lock held":
        held = hold_lock(env)
    before = hub_main(env)
    done = dl(chg, env, "land", pr)
    assert stopped(done, "stopped"), done.stdout + done.stderr
    assert hub_main(env) == before
    del held


@pytest.mark.xfail(strict=True, reason="AT H-L7: a torn record line is a traceback or "
                   "'bad body', never naming the file and line")
@pytest.mark.parametrize("name", ["host.jsonl", "review-threads.jsonl"])
@pytest.mark.parametrize("verb", ["land", "threads"])
def test_l7_a_torn_record_is_one_line(name: str, verb: str, chg: Path,
                                      env: dict[str, str]) -> None:
    pr = ready(chg, env)
    host(chg, env, "thread-post", {"pr": pr, "text": "`crid:c3`"})
    path = chg / CHANGE / name
    text = path.read_text(encoding="utf-8")
    path.write_text(text[:-10], encoding="utf-8")  # the last line torn mid-record
    torn = path.read_text(encoding="utf-8")
    if verb == "land":
        done = dl(chg, env, "land", pr)
    else:
        done = dl(chg, env, "host", "threads", "--json", stdin=json.dumps({"pr": pr}))
    said = (done.stdout + done.stderr).strip()
    assert "Traceback" not in said and len(said.splitlines()) == 1, said
    assert name in said and "line" in said, said
    assert path.read_text(encoding="utf-8") == torn  # nothing lost


def test_l8_concurrent_thread_writes_lose_nothing(chg: Path, env: dict[str, str]) -> None:
    pr = open_pr(chg, env)
    ids = [host(chg, env, "thread-post", {"pr": pr, "text": f"`crid:{n}`"})["id"]
           for n in range(10)]
    full = {**env, "CLAUDE_PROJECT_DIR": str(chg)}
    calls = [subprocess.Popen(["bash", str(DL), "host", "thread-status", "--json"], cwd=chg,
                              env=full, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True) for _ in ids]
    codes = []
    for call, tid in zip(calls, ids, strict=True):
        _, err = call.communicate(json.dumps({"pr": pr, "thread": tid, "status": "fixed"}),
                                  timeout=120)
        codes.append((call.returncode, err.strip()))
    assert all(code == 0 for code, _ in codes), codes
    assert git(chg, env, "status", "--porcelain") == ""  # every write committed
    statuses = [t["status"] for t in host(chg, env, "threads", {"pr": pr})]
    assert statuses == ["fixed"] * 10


def test_l11_the_whole_loop_on_a_remote_called_hub(tmp_path: Path, env: dict[str, str]) -> None:
    tree = make_chg(make_local(tmp_path, env, "hub"), env)
    verified = dl(tree, env, "verify", "demo")
    assert verified.returncode == 0, verified.stdout + verified.stderr
    git(tree, env, "add", "-A")
    git(tree, env, "commit", "-qm", "verify")
    pr = open_pr(tree, env)
    review = dl(tree, env, "review", extra={"PR_ID": pr, "STUB_MODE": "json"})
    assert review.returncode == 0, (review.stdout + review.stderr)[-2000:]
    finish(tree, env, [row for row in DONE if row[0] != "verify"])
    clean(tree, env, pr)
    landed = dl(tree, env, "land", pr)
    assert landed.returncode == 0, landed.stdout + landed.stderr
    assert hub_main(env) == git(tree, env, "rev-parse", "HEAD")


@pytest.mark.parametrize("remote", ["origin", "hub"])
def test_l11_done_guard_names_the_missing_lines(remote: str, tmp_path: Path,
                                                env: dict[str, str]) -> None:
    tree = make_chg(make_local(tmp_path, env, remote), env)  # finished: every task ticked
    full = {**env, "CLAUDE_PROJECT_DIR": str(tree), "CLAUDE_PLUGIN_ROOT": str(PLUGIN)}
    done = subprocess.run(["sh", str(PLUGIN / "hooks" / "done-guard.sh")], cwd=tree, env=full,
                          input="{}", capture_output=True, text=True, timeout=120, check=False)
    assert done.returncode == 2, done.stderr
    assert "demo" in done.stderr and "a green verify record" in done.stderr, done.stderr


def test_l9_the_docs_say_the_host_body_comes_on_stdin() -> None:
    readme = (PLUGIN.parents[1] / "README.md").read_text(encoding="utf-8")
    assert "`dl host <verb> --json`" in readme and "body on stdin" in readme


def test_l9_host_never_waits_on_a_silent_stdin(chg: Path, env: dict[str, str]) -> None:
    open_pr(chg, env)
    full = {**env, "CLAUDE_PROJECT_DIR": str(chg)}
    call = subprocess.Popen(["bash", str(DL), "host", "threads", "--json"], cwd=chg, env=full,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        call.wait(timeout=20)
    except subprocess.TimeoutExpired:
        call.kill()
        call.wait(timeout=10)
        pytest.fail("still waiting on stdin after 20 s")
    finally:
        assert call.stdin is not None
        call.stdin.close()


def bounded(cwd: Path, env: dict[str, str], *args: str, seconds: int = 30) -> str:
    """Run dl in its own process group; kill the group after `seconds`; return its output."""
    full = {**env, "CLAUDE_PROJECT_DIR": str(cwd), "DEV_LOOP_WORKER": "w1"}
    call = subprocess.Popen(["bash", str(DL), *args], cwd=cwd, env=full, text=True,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, start_new_session=True)
    try:
        out, _ = call.communicate(timeout=seconds)
    except subprocess.TimeoutExpired:
        os.killpg(call.pid, signal.SIGKILL)
        out, _ = call.communicate(timeout=10)
        return f"still running after {seconds} s\n{out}"
    return f"exit {call.returncode}\n{out}"


@pytest.mark.parametrize("argv", [["tasks", "watch", "--once"], ["worker"]], ids=" ".join)
def test_l10_tasks_on_the_local_host_is_one_line(argv: list[str], local: Path,
                                                 env: dict[str, str], tmp_path: Path) -> None:
    edit_config(local, lambda c: c.update(workers={"w1": {
        "checkout": str(local), "worktrees": str(tmp_path / "wt"), "never": []}}))
    git(local, env, "config", "core.hooksPath", ".githooks")  # dl doctor green for the worker
    said = bounded(local, env, *argv).strip()
    assert said.startswith("exit 1") and "Traceback" not in said, said
    assert len(said.splitlines()) == 2 and "ado" in said.lower() and "github" in said.lower()
