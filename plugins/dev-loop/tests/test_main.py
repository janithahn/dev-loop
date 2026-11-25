"""The `dl` command: every name dispatches; a missing module exits 2; bootstrap is idempotent."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(PLUGIN / "src"), str(Path(__file__).parent)]

import test_neutral  # noqa: E402
from dev_loop import __main__ as entry  # noqa: E402
NAMES = [
    "record", "records", "lesson", "review", "host", "ci", "stack-rebase", "githook", "init",
    "doctor", "config", "propose", "tasklist", "verify", "pr", "status", "archive", "tasks",
    "worker", "dashboard", "fleet-export", "land", "incident", "clean",
]


def test_table_holds_every_name() -> None:
    assert sorted(entry.TABLE) == sorted(NAMES)


@pytest.mark.parametrize("name", NAMES)
def test_every_name_dispatches(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.ModuleType("fake")
    module.main = lambda argv: 7 if argv == ["x"] else 99  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, f"dev_loop.{entry.TABLE[name]}", module)
    assert entry.main([name, "x"]) == 7


def test_missing_module_exits_2_with_one_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setitem(entry.TABLE, "doctor", "no_such_module")
    assert entry.main(["doctor"]) == 2
    err = capsys.readouterr().err
    assert err == "dl doctor: not built yet\n"


def test_unknown_or_empty_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert entry.main([]) == 2
    assert entry.main(["nope"]) == 2
    assert len(capsys.readouterr().err.splitlines()) == 2


def test_bootstrap_twice_is_a_no_op(tmp_path: Path) -> None:
    python = tmp_path / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\necho called >> \"$0.log\"\n")
    python.chmod(0o755)
    (tmp_path / "venv" / ".ready").touch()
    env = {**os.environ, "CLAUDE_PLUGIN_DATA": str(tmp_path), "CLAUDE_PLUGIN_ROOT": str(PLUGIN)}
    outs = [
        subprocess.run(["bash", str(PLUGIN / "bin" / "dl-bootstrap")], env=env,
                       capture_output=True, text=True, check=True).stdout
        for _ in range(2)
    ]
    assert outs[0] == outs[1]
    assert len(outs[0].splitlines()) == 1
    assert not (python.parent / "python.log").exists()


def test_launcher_uses_venv_python(tmp_path: Path) -> None:
    python = tmp_path / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\necho venv \"$@\"\n")
    python.chmod(0o755)
    (tmp_path / "venv" / ".ready").touch()
    env = {**os.environ, "CLAUDE_PLUGIN_DATA": str(tmp_path), "CLAUDE_PLUGIN_ROOT": str(PLUGIN)}
    out = subprocess.run(["bash", str(PLUGIN / "bin" / "dl"), "x"], env=env,
                         capture_output=True, text=True, check=True).stdout
    assert out.strip() == "venv -m dev_loop x"


def test_new_files_are_neutral() -> None:
    files = ["bin/dl", "bin/dl-bootstrap", "src/dev_loop/__main__.py", "tests/test_main.py"]
    assert test_neutral.check([PLUGIN / f for f in files]) == []


def test_launcher_ignores_a_half_made_venv(tmp_path: Path) -> None:
    python = tmp_path / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\necho venv \"$@\"\n")
    python.chmod(0o755)  # no .ready: bootstrap did not finish
    env = {**os.environ, "CLAUDE_PLUGIN_DATA": str(tmp_path), "CLAUDE_PLUGIN_ROOT": str(PLUGIN)}
    done = subprocess.run(["bash", str(PLUGIN / "bin" / "dl"), "x"], env=env,
                          capture_output=True, text=True, check=False)
    assert "venv" not in done.stdout
    assert done.returncode == 2  # the real dispatcher ran from the plugin source: unknown name


def test_launcher_runs_from_an_installed_copy(tmp_path: Path) -> None:
    """The installed plugin holds the plugin folder only (no pyproject.toml): dl still runs."""
    shutil.copytree(PLUGIN / "bin", tmp_path / "bin")
    shutil.copytree(PLUGIN / "src", tmp_path / "src")
    env = {**os.environ, "CLAUDE_PLUGIN_ROOT": str(tmp_path)}
    env.pop("CLAUDE_PLUGIN_DATA", None)
    done = subprocess.run(["bash", str(tmp_path / "bin" / "dl"), "x"], env=env,
                          capture_output=True, text=True, check=False)
    assert done.returncode == 2
    assert "No module named" not in done.stderr


@pytest.mark.parametrize("name", ["incident", "status", "tasklist"])
def test_a_bad_config_file_stops_every_command(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "dev-loop.yaml").write_text("caps: {enabled: maybe}\n", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    with pytest.raises(SystemExit) as stopped:  # a string: printed on stderr, exit 1
        entry.main([name, "x"])
    said = str(stopped.value)
    assert len(said.splitlines()) == 1 and "dev-loop.yaml" in said and "caps.enabled" in said


def test_a_missing_config_file_still_means_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    assert entry.main(["incident", "--help"]) == 0


FAKE_PYTHON3 = """#!/bin/sh
# A python3 of version 3.99 that makes a venv of itself and installs nothing (no network).
case "$1" in
  -c) echo 3.99 ;;
  -m) if [ "$2" = venv ]; then
        mkdir -p "$3/bin" && cp "$0" "$3/bin/python" && echo "version = 3.99.1" > "$3/pyvenv.cfg"
      fi
      if [ "$2" = pip ]; then exit "${PIP_EXIT:-0}"; fi ;;
esac
"""


def test_bootstrap_rebuilds_a_venv_made_for_another_python(tmp_path: Path) -> None:
    """A venv marked ready but built on another Python than the hooks' python3 (as `uv venv` may
    build it) is rebuilt with python3; one built on python3's version is kept."""
    fake = tmp_path / "fakebin" / "python3"
    fake.parent.mkdir()
    fake.write_text(FAKE_PYTHON3)
    fake.chmod(0o755)
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = /usr/bin\nversion_info = 3.12.3\n")
    (venv / ".ready").touch()
    env = {**os.environ, "CLAUDE_PLUGIN_DATA": str(tmp_path), "CLAUDE_PLUGIN_ROOT": str(PLUGIN),
           "PATH": f"{fake.parent}{os.pathsep}{os.environ['PATH']}"}
    boot = ["bash", str(PLUGIN / "bin" / "dl-bootstrap")]
    first = subprocess.run(boot, env=env, capture_output=True, text=True, check=True)
    assert "environment created" in first.stdout, first
    assert "version = 3.99.1" in (venv / "pyvenv.cfg").read_text()
    again = subprocess.run(boot, env=env, capture_output=True, text=True, check=True)
    assert "environment ready" in again.stdout, again


def test_a_failed_rebuild_keeps_the_working_venv(tmp_path: Path) -> None:
    """The venv is rebuilt (another Python) but pip cannot install and uv cannot either: the old
    venv, which the hooks can still read PyYAML from, stays whole; nothing half made is left."""
    fake = tmp_path / "fakebin" / "python3"
    fake.parent.mkdir()
    fake.write_text(FAKE_PYTHON3)
    fake.chmod(0o755)
    (fake.parent / "uv").write_text("#!/bin/sh\nexit 1\n")
    (fake.parent / "uv").chmod(0o755)
    venv = tmp_path / "venv"
    kept = venv / "lib" / "python3.12" / "site-packages" / "yaml" / "__init__.py"
    kept.parent.mkdir(parents=True)
    kept.write_text("")
    (venv / "pyvenv.cfg").write_text("home = /usr/bin\nversion_info = 3.12.3\n")
    (venv / ".ready").touch()
    env = {**os.environ, "CLAUDE_PLUGIN_DATA": str(tmp_path), "CLAUDE_PLUGIN_ROOT": str(PLUGIN),
           "PATH": f"{fake.parent}{os.pathsep}{os.environ['PATH']}", "PIP_EXIT": "1"}
    boot = ["bash", str(PLUGIN / "bin" / "dl-bootstrap")]
    done = subprocess.run(boot, env=env, capture_output=True, text=True, check=False)
    assert done.returncode == 1 and "stays" in done.stderr, done
    assert kept.is_file() and (venv / ".ready").is_file()
    assert "3.12.3" in (venv / "pyvenv.cfg").read_text()
    assert sorted(path.name for path in tmp_path.iterdir()) == ["fakebin", "venv"]
    (venv / ".ready").unlink()  # a venv never finished is no loss: a failed build removes it
    done = subprocess.run(boot, env=env, capture_output=True, text=True, check=False)
    assert done.returncode == 1 and not venv.exists(), done


def test_version_prints_the_plugin_json_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "plugin.json").write_text('{"version": "9.8.7"}')
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path))
    assert entry.main(["--version"]) == 0
    assert capsys.readouterr().out == "dl 9.8.7\n"
    (tmp_path / ".claude-plugin" / "plugin.json").unlink()
    assert entry.main(["--version"]) == 1
    assert len(capsys.readouterr().err.splitlines()) == 1


def test_help_names_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert entry.main(["-h"]) == 0
    assert "--version" in capsys.readouterr().out
