"""Acceptance batch 3, the doctor's rows, run through `bin/dl` (harness: batch3.py).

A row that fails on today's code is a strict xfail naming its symptom, so a fix flips it.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from batch3 import SECRET, dl, edit, env, git, local, path_without, reds  # noqa: F401


def test_d1_doctor_green_on_a_good_project(local: Path, env: dict[str, str]) -> None:
    done = dl(local, env, "doctor")
    assert done.code == 0, done.out
    assert "RED" not in done.out
    assert reds(local, env) == {}


def _stale(work: Path, env: dict[str, str]) -> None:
    """The checkout's merge base with origin/main is 10 days older than origin/main's tip."""
    other, old = work.parent / "other", str(int(time.time()) - 10 * 86400)
    git(work.parent, env, "clone", "-q", "hub.git", str(other))
    (other / "a.txt").write_text("a\n", encoding="utf-8")
    git(other, env, "add", "-A")
    git(other, {**env, "GIT_AUTHOR_DATE": f"@{old}", "GIT_COMMITTER_DATE": f"@{old}"},
        "commit", "-qm", "old")
    git(other, env, "push", "-q", "origin", "main")
    git(work, env, "pull", "-q", "origin", "main")
    (other / "b.txt").write_text("b\n", encoding="utf-8")
    git(other, env, "add", "-A")
    git(other, env, "commit", "-qm", "new")
    git(other, env, "push", "-q", "origin", "main")
    git(work, env, "fetch", "-q", "origin")


def _unrules(work: Path, _env: dict[str, str]) -> None:
    for name in ("CLAUDE.md", "AGENTS.md"):
        lines = (work / name).read_text(encoding="utf-8").splitlines()
        kept = [line for line in lines if "RULES.md" not in line]
        (work / name).write_text("\n".join(kept) + "\n", encoding="utf-8")


def _unpin(work: Path, _env: dict[str, str]) -> None:
    path = work / ".claude" / "settings.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["extraKnownMarketplaces"]["dev-loop"]["source"]["ref"] = "v0.0.1"
    path.write_text(json.dumps(doc), encoding="utf-8")


def _bad_config(work: Path, _env: dict[str, str]) -> None:
    edit(work, {"size": {"cap": "not-a-number"}})


def _bad_remote(work: Path, env: dict[str, str]) -> None:
    git(work, env, "remote", "set-url", "origin", str(work.parent / "missing.git"))


def _follow(work: Path, env: dict[str, str], fix: str) -> None:
    """Follow a row's one-line fix the way a user would read it."""
    if fix == "run dl init":
        assert dl(work, env, "init", "--host", "local").code is not None
    elif fix.startswith("dev-loop.yaml:"):  # the error names the key; set it right
        edit(work, {"size": {"cap": 200}})
    elif fix.endswith("git pull"):
        git(work, env, "pull", "-q", "origin", "main")
    elif "remote" in fix:
        git(work, env, "remote", "set-url", "origin", str(work.parent / "hub.git"))


BREAKS = [
    pytest.param(_bad_config, "dev-loop.yaml", id="config"),
    pytest.param(_unrules, "rules imported", id="rules"),
    pytest.param(_unpin, "plugin version", id="pin"),
    pytest.param(_bad_remote, "host reachable", id="remote"),
    pytest.param(_stale, "checkout", id="stale-checkout"),
]


@pytest.mark.parametrize(("breaker", "row"), BREAKS)
def test_d2_one_red_row_alone_and_its_fix_works(
    local: Path, env: dict[str, str], breaker: Any, row: str,
) -> None:
    breaker(local, env)
    red = reds(local, env)
    assert set(red) == {row}, red
    assert dl(local, env, "doctor").code == 1
    _follow(local, env, red[row])
    assert reds(local, env) == {}, f"fix followed: {red[row]}"


def test_d2_disk_threshold_red_alone_and_dl_clean_runs(local: Path, env: dict[str, str]) -> None:
    """The fix `dl clean` cannot free the host's own disk, so it is shown to run, not to cure."""
    edit(local, {"cleanup": {"threshold_pct": 1}})
    red = reds(local, env)
    assert set(red) == {"disk"}, red
    assert red["disk"] == "run dl clean"
    assert dl(local, env, "doctor").code == 1
    assert dl(local, env, "clean").code == 0


def test_d3_unset_hooks_path_and_empty_steps_are_two_reds_each_fixed(
    local: Path, env: dict[str, str],
) -> None:
    git(local, env, "config", "--unset", "core.hooksPath")
    edit(local, {"verify": {"steps": {}}})
    red = reds(local, env)
    assert red == {"githooks": "git config core.hooksPath .githooks",
                   "verify steps": "add a command under verify.steps in dev-loop.yaml"}, red
    git(local, env, *red["githooks"].split()[1:])
    edit(local, {"verify": {"steps": {"unit": "true"}}})
    assert reds(local, env) == {}


@pytest.fixture
def silent_port() -> int:
    """A TCP port on loopback that accepts connections and never answers."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    held: list[socket.socket] = []
    threading.Thread(target=lambda: held.extend(iter(lambda: server.accept()[0], None)),
                     daemon=True).start()
    return int(server.getsockname()[1])


def test_d4_doctor_finishes_on_an_unanswering_remote(
    local: Path, env: dict[str, str], silent_port: int,
) -> None:
    git(local, env, "remote", "set-url", "origin", f"git://127.0.0.1:{silent_port}/x.git")
    done = dl(local, env, "doctor", timeout=60)
    assert done.code == 1, f"killed after {done.seconds:.0f} s"
    assert "RED  host reachable" in done.out


def test_d5_doctor_prints_no_token_from_the_env(local: Path, env: dict[str, str]) -> None:
    edit(local, {"host": {"provider": "ado", "org": "o", "project": "p", "repo": "r"}})
    with socket.socket() as probe:  # a port nothing listens on: the host call fails fast
        probe.bind(("127.0.0.1", 0))
        url = f"http://127.0.0.1:{probe.getsockname()[1]}"
    ado = {**env, "DEV_LOOP_HOST_TOKEN": SECRET, "DEV_LOOP_HOST_URL": url}
    for args in (["doctor"], ["doctor", "--json"]):
        done = dl(local, ado, *args)
        assert done.code is not None
        assert "credential" in done.out
        assert SECRET not in done.out


@pytest.mark.parametrize("tool", ["jq", "flock", "claude"])
def test_d6_doctor_names_a_missing_tool(
    local: Path, env: dict[str, str], tmp_path: Path, tool: str,
) -> None:
    done = dl(local, {**env, "PATH": path_without(tool, tmp_path)}, "doctor")
    assert done.code == 1, done.out
    assert tool in done.out


def test_d6_doctor_names_a_missing_gh_on_github(
    local: Path, env: dict[str, str], tmp_path: Path,
) -> None:
    edit(local, {"host": {"provider": "github"}})
    done = dl(local, {**env, "PATH": path_without("gh", tmp_path)}, "doctor")
    assert done.code == 1
    assert "RED  host reachable   gh not found" in done.out
