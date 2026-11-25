"""Acceptance batch 1: install, upgrade, rollback.

Every row runs as a user would: shell steps through subprocess under `timeout`, in a throwaway
HOME, CLAUDE_CONFIG_DIR, CLAUDE_PLUGIN_DATA and git config, never touching a real project. A row
that fails on today's code is a strict xfail naming its symptom, so a fix flips it.

The rows that install through the `claude` CLI skip when it is missing (`DL_AT_CLAUDE` names it).
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import socket
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from batch1_harness import CLAUDE, NOT_RUN, ROOT, Harness, P, market_at, needs_claude


NO_VENV = importlib.util.find_spec("ensurepip") is None and shutil.which("uv") is None
needs_venv = pytest.mark.skipif(NO_VENV, reason="not run: this host can build no venv")


@pytest.fixture
def h(tmp_path: Path) -> Harness:
    return Harness(tmp_path)


# Clean install ------------------------------------------------------------


@needs_claude
@needs_venv
def test_i1_clean_install_builds_the_venv_on_first_session(h: Harness) -> None:
    """A clean install, then the cache it made. The session is not logged in: the
    SessionStart hooks still run, which is all the row needs from it."""
    assert h.claude("plugin", "marketplace", "add", str(ROOT)).returncode == 0
    done = h.claude("plugin", "install", "dev-loop@dev-loop")
    assert done.returncode == 0, done.stdout + done.stderr
    version = (P / ".claude-plugin/plugin.json").read_text().split('"version": "')[1].split('"')[0]
    cache = h.cache(version)
    assert cache.is_dir()
    env = {k: v for k, v in h.env.items() if k != "CLAUDE_PLUGIN_DATA"}
    h.sh([str(CLAUDE), "-p", "/dev-loop:status", "--max-turns", "1"], h.run_dir, 180, env)
    assert list((h.home / ".claude/plugins/data").glob("dev-loop*/venv/.ready"))
    scripts = [p for p in cache.rglob("*") if p.suffix == ".sh" or p.name in ("dl", "dl-bootstrap")]
    assert scripts
    assert [p for p in scripts if not os.access(p, os.X_OK)] == []


# Bootstrap twice ------------------------------------------------------------


def fake_python(h: Harness) -> Path:
    """A `python3` whose venv module makes a venv whose pip only logs: no network needed."""
    folder = h.run_dir / "fakebin"
    folder.mkdir()
    venv_python = '#!/bin/sh\necho "$@" >> "$(dirname "$0")/../pip.log"\n'
    (folder / "python3").write_text(
        '#!/bin/sh\n[ "$1 $2" = "-m venv" ] || exit 9\nmkdir -p "$3/bin"\n'
        f"cat > \"$3/bin/python\" <<'EOF'\n{venv_python}EOF\nchmod +x \"$3/bin/python\"\n")
    (folder / "python3").chmod(0o755)
    return folder


def test_i2_bootstrap_is_idempotent_and_rebuilds_without_ready(h: Harness) -> None:
    """Twice-is-a-no-op and the half-made venv are also in test_main.py."""
    env = h.env | {"PATH": f"{fake_python(h)}{os.pathsep}{h.env['PATH']}"}
    boot = ["bash", str(P / "bin/dl-bootstrap")]
    first, second = (h.sh(boot, h.run_dir, env=env) for _ in range(2))
    assert first.returncode == second.returncode == 0, first.stderr
    assert "created" in first.stdout and "ready" in second.stdout
    (h.data / "venv/.ready").unlink()
    third = h.sh(boot, h.run_dir, env=env)
    assert third.returncode == 0 and "created" in third.stdout
    assert (h.data / "venv/.ready").exists()
    assert (h.data / "venv/pip.log").read_text().count("install") == 1  # rebuilt from scratch


# Offline ------------------------------------------------------------


@pytest.fixture
def blackhole() -> Iterator[str]:
    """A URL whose server accepts and never answers: a network that drops every packet."""
    server = socket.create_server(("127.0.0.1", 0))
    held: list[socket.socket] = []
    threading.Thread(target=lambda: [held.append(server.accept()[0]) for _ in iter(int, 1)],
                     daemon=True).start()
    yield f"http://127.0.0.1:{server.getsockname()[1]}"
    server.close()


def test_i3_no_network_does_not_hang_session_start(h: Harness, blackhole: str) -> None:
    """The venv removed, every index and proxy a black hole."""
    env = h.env | {name: blackhole for name in (
        "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY")}
    hang = h.run_dir / "hangbin"  # python3 and uv that never return: the hang path always runs
    hang.mkdir()
    for name in ("python3", "uv"):
        (hang / name).write_text("#!/bin/sh\nexec sleep 600\n")
        (hang / name).chmod(0o755)
    env["PATH"] = os.pathsep.join([str(hang), env["PATH"]])
    env |= {"PIP_INDEX_URL": f"{blackhole}/simple", "UV_DEFAULT_INDEX": f"{blackhole}/simple",
            "UV_INDEX_URL": f"{blackhole}/simple", "PIP_CONFIG_FILE": os.devnull}
    done = h.sh(["bash", str(P / "bin/dl-bootstrap")], h.run_dir, limit=150, env=env)  # two 60 s steps
    assert done.returncode not in (0, 124), done.returncode
    assert len(done.stderr.strip().splitlines()) == 1, done.stderr
    assert not (h.data / "venv/.ready").exists()
    assert h.dl(h.run_dir, "--help").returncode == 0  # dl still runs on a Python with PyYAML


# No plugin environment ------------------------------------------------------------


@needs_venv
def test_i4_dl_works_outside_claude_code(h: Harness) -> None:
    """No plugin env vars, a python3 without PyYAML, the plugin's bin on PATH."""
    proj = h.local()
    nopy = h.run_dir / "nopy"
    nopy.mkdir()
    for name in ("python3", "python"):  # -I -S: no site-packages, so no PyYAML
        (nopy / name).write_text(f'#!/bin/sh\nexec "{sys.executable}" -I -S "$@"\n')
        (nopy / name).chmod(0o755)
    outside = {k: v for k, v in h.env.items() if k not in ("CLAUDE_PLUGIN_DATA", "DL_PYTHON")}
    outside["PATH"] = os.pathsep.join([str(nopy), str(P / "bin"), h.env["PATH"]])
    doctor = h.sh(["dl", "doctor"], proj, env=outside)
    fix = doctor.stderr.strip().rpartition("; run ")[2]
    if doctor.returncode == 2 and fix:  # it named one fix: that fix must work
        assert h.sh(fix.split(), proj, env=outside).returncode == 0, "the named fix fails"
    (proj / "notes.txt").write_text("x\n")
    h.sh(["git", "switch", "-qc", "work"], proj, env=outside)
    h.sh(["git", "add", "notes.txt"], proj, env=outside)
    done = h.sh(["git", "commit", "-qm", "notes"], proj, env=outside)
    assert done.returncode == 0, done.stderr


# Upgrade and rollback through the CLI ------------------------------------------


def test_i6_the_upgrade_is_documented() -> None:
    """The manual read: README names how a project moves to a new release."""
    text = (ROOT / "README.md").read_text(encoding="utf-8").lower()
    if "upgrad" not in text and "plugin update" not in text:
        pytest.xfail("AT I-6: README has no upgrade steps; init pins a fixed ref, auto-update off")
    assert "upgrad" in text or "plugin update" in text


def install_and_change(h: Harness, version: str) -> tuple[Path, Path]:
    """Install `version` from a local marketplace; F-LOCAL with its dl; one change with records."""
    assert h.claude("plugin", "marketplace", "add", str(market_at(h, version))).returncode == 0
    assert h.claude("plugin", "install", "dev-loop@dev-loop").returncode == 0
    plugin = h.cache(version)
    proj = h.local(plugin)
    assert h.dl(proj, "propose", "demo", plugin=plugin).returncode == 0
    work = h.run_dir / "proj-wt/demo"
    record = ["record", "demo", "follow-up", "pr=1", "thread=2", "file=a.py",
              "finding=a minor", "why=later"]
    done = h.dl(work, *record, plugin=plugin)
    assert done.returncode == 0, done.stderr
    h.commit(work, "demo records")
    return proj, work


def move_to(h: Harness, version: str) -> Path:
    """The upgrade (or rollback) a user can run: marketplace update, plugin update."""
    market_at(h, version)
    assert h.claude("plugin", "marketplace", "update", "dev-loop").returncode == 0
    done = h.claude("plugin", "update", "dev-loop@dev-loop")
    assert done.returncode == 0, done.stdout + done.stderr
    assert h.cache(version).is_dir(), sorted(p.name for p in h.cache(version).parent.iterdir())
    return h.cache(version)


def outputs(h: Harness, proj: Path, work: Path, plugin: Path) -> list[str]:
    runs = [h.dl(work, "records", "follow-ups", plugin=plugin), h.dl(proj, "status", plugin=plugin)]
    return [run.stdout for run in runs]


@needs_claude
def test_i6_i7_upgrade_keeps_records_and_config(h: Harness) -> None:
    """Scripted: 0.4.0 -> 0.4.1 by plugin update, then dl init."""
    proj, work = install_and_change(h, "0.4.0")
    config = (proj / "dev-loop.yaml").read_bytes()
    before = outputs(h, proj, work, h.cache("0.4.0"))
    plugin = move_to(h, "0.4.1")
    assert h.dl(proj, "init", plugin=plugin).returncode == 0
    changed = h.sh(["git", "status", "--porcelain"], proj).stdout.split("\n")
    allowed = (".dev-loop/RULES.md", ".githooks/", ".claude/settings.json")  # the pin moves
    assert [line for line in changed if line and not line[3:].startswith(allowed)] == []
    doctor = h.dl(proj, "doctor", plugin=plugin)
    assert "RED  plugin version" not in doctor.stdout, doctor.stdout
    pin = h.dl(proj, "ci", "pin-check", plugin=plugin)
    assert pin.returncode == 0, pin.stdout + pin.stderr
    assert outputs(h, proj, work, plugin) == before
    assert (proj / "dev-loop.yaml").read_bytes() == config


@needs_claude
def test_i8_rollback_works_or_refuses_in_one_line(h: Harness) -> None:
    """Back from 0.4.1 to 0.4.0 on a local-host project."""
    proj, _ = install_and_change(h, "0.4.1")
    plugin = move_to(h, "0.4.0")
    for args in (("doctor",), ("config", "get", "host.provider")):
        done = h.dl(proj, *args, plugin=plugin)
        assert "Traceback" not in done.stdout + done.stderr, args
        assert done.returncode == 0 or done.stderr.count("\n") <= 1, (args, done.stderr)
    provider = h.dl(proj, "config", "get", "host.provider", plugin=plugin)
    assert provider.stdout.strip() == "local", provider.stdout


# An old config version ------------------------------------------------------------

NEWER = "dev-loop.yaml is version 2"


def newer(h: Harness) -> tuple[Path, Path]:
    """F-CHG made before the config moved to version 2."""
    proj = h.local()
    assert h.dl(proj, "propose", "demo").returncode == 0
    work = h.run_dir / "proj-wt/demo"
    for path in (proj, work):
        text = (path / "dev-loop.yaml").read_text()
        (path / "dev-loop.yaml").write_text(text.replace("version: 1", "version: 2", 1))
        h.commit(path, "config version 2")
    return proj, work


def state(h: Harness, proj: Path) -> list[str]:
    git = (["status", "--porcelain"], ["branch", "-a"], ["worktree", "list"], ["ls-remote", "origin"])
    return [h.sh(["git", *args], proj).stdout for args in git]


@pytest.mark.parametrize("args", [
    pytest.param(("land",), id="land"),
    pytest.param(("config", "get", "host.provider"), id="config-get"),
    pytest.param(("pr", "demo"), id="pr"),
    pytest.param(("propose", "other"), id="propose"),
    pytest.param(("verify", "demo"), id="verify"),
])
def test_i9_a_newer_config_is_refused_in_one_line(h: Harness, args: tuple[str, ...]) -> None:
    """Each command says the config is version 2, exits non-zero, does nothing."""
    proj, work = newer(h)
    where = proj if args[0] in ("land", "config", "propose") else work
    before = state(h, proj)
    done = h.dl(where, *args)
    assert done.returncode != 0, done.stdout
    assert NEWER in done.stderr and "update the plugin" in done.stderr, done.stderr
    assert len(done.stderr.strip().splitlines()) == 1, done.stderr
    assert state(h, proj) == before


# Uninstall the git hook ------------------------------------------------------------


@needs_claude
def test_i10_uninstall_leaves_the_project_usable(h: Harness) -> None:
    """test_init_githook.py covers a missing dl; this uninstalls a real one."""
    assert h.claude("plugin", "marketplace", "add", str(ROOT)).returncode == 0
    assert h.claude("plugin", "install", "dev-loop@dev-loop").returncode == 0
    proj = h.local()
    assert h.claude("plugin", "uninstall", "dev-loop@dev-loop").returncode == 0
    h.sh(["git", "switch", "-qc", "work"], proj)
    (proj / "notes.txt").write_text("x\n")
    h.sh(["git", "add", "notes.txt"], proj)
    done = h.sh(["git", "commit", "-qm", "notes"], proj)
    assert done.returncode == 0, done.stderr
    assert NOT_RUN in done.stderr, done.stderr
