"""Acceptance batch 0: the baseline suite.

The shell and node self-checks under tests/, which no pytest file or CI step runs.
The suite is sharded wherever it gates; set DL_AT_TIMING=1 to time the sharded run itself.
`claude plugin validate --strict` on the plugin and the repository. The CLI is CLAUDE_BIN,
or `claude` on PATH; without one that starts, the test is skipped with the reason named.
Every command runs under an isolated HOME and CLAUDE_CONFIG_DIR, with a timeout.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest
import yaml

TESTS = Path(__file__).resolve().parents[1]
PLUGIN = TESTS.parent
ROOT = PLUGIN.parents[1]
SHELL_CHECKS = ["test-done-guard.sh", "test-size-cap.sh", "test-stack-rebase.sh"]
SUITE = "pytest plugins/dev-loop/tests"
MIN_CLI = (2, 1, 325)  # the first CLI that knows every plugin.json field the plugin uses


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    """The harness preamble: HOME, Claude config, plugin data and git config all under tmp."""
    home = tmp_path / "home"
    values = {**os.environ, "HOME": str(home), "CLAUDE_CONFIG_DIR": str(home / ".claude"),
              "CLAUDE_PLUGIN_DATA": str(tmp_path / "data"),
              "GIT_CONFIG_GLOBAL": str(tmp_path / "gitconfig"), "GIT_TERMINAL_PROMPT": "0"}
    (home / ".claude").mkdir(parents=True)
    (tmp_path / "data").mkdir()
    for key, value in [("user.email", "t@noreply"), ("user.name", "tester"),
                       ("init.defaultBranch", "main")]:
        subprocess.run(["git", "config", "--global", key, value], env=values,  # noqa: S603, S607
                       check=True, timeout=30)
    return values


def run(argv: list[str], env: dict[str, str], cwd: Path = ROOT,
        timeout: int = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True,  # noqa: S603
                          check=False, timeout=timeout)


@pytest.mark.parametrize("script", SHELL_CHECKS)
def test_b0_1_shell_self_check_passes(script: str, env: dict[str, str]) -> None:
    result = run(["bash", str(TESTS / script)], env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().endswith("ok"), result.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node is absent")
@pytest.mark.xfail(strict=True, reason="AT B0-1: test_dev_loop_render.mjs imports "
                   "./dev_loop/dashboard/page from tests/, not ../src; ERR_MODULE_NOT_FOUND")
def test_b0_1_node_render_test_passes_where_it_ships(env: dict[str, str]) -> None:
    result = run(["node", "--test", "test_dev_loop_render.mjs"], env, cwd=TESTS)
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]


def _suite_lines(texts: list[str]) -> list[str]:
    lines = [line for text in texts for line in text.splitlines() if SUITE in line]
    assert lines, "no step runs the suite"
    return lines


def test_b0_2_the_repo_gate_runs_the_suite_sharded() -> None:
    config = yaml.safe_load((ROOT / "dev-loop.yaml").read_text(encoding="utf-8"))
    for line in _suite_lines(list(config["verify"]["steps"].values())):
        assert " -n " in f"{line} " and "pytest-xdist" in line, line


def test_b0_2_ci_runs_the_suite_sharded() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    for line in _suite_lines([workflow]):
        assert " -n " in f"{line} ", line
    assert "pytest-xdist" in workflow


@pytest.mark.skipif(os.environ.get("DL_AT_TIMING") != "1", reason="set DL_AT_TIMING=1 to time")
def test_b0_2_the_sharded_suite_fits_600_seconds(env: dict[str, str]) -> None:
    argv = ["uv", "run", "-q", "--no-project", "--with", "pytest", "--with", "pyyaml",
            "--with", "pytest-xdist", "python", "-m", "pytest", "plugins/dev-loop/tests",
            "-q", "-n", "auto", "--ignore", str(TESTS / "acceptance")]
    start = time.monotonic()
    result = run(argv, env, timeout=600)
    assert result.returncode == 0, result.stdout[-2000:]
    assert time.monotonic() - start < 600


def _claude(env: dict[str, str]) -> str:
    cli = os.environ.get("CLAUDE_BIN") or shutil.which("claude")
    if cli is None:
        pytest.skip("AT B0-4 not run: no claude CLI (set CLAUDE_BIN)")
    version = run([cli, "--version"], env, timeout=120)
    if version.returncode != 0:
        pytest.skip(f"AT B0-4 not run: {cli} fails under an isolated HOME (set CLAUDE_BIN)")
    found = re.search(r"(\d+)\.(\d+)\.(\d+)", version.stdout)
    if found is None or tuple(map(int, found.groups())) < MIN_CLI:
        pytest.skip(f"AT B0-4 not run: claude CLI {version.stdout.strip()} is below 2.1.325")
    return cli


@pytest.mark.parametrize("target", ["plugins/dev-loop", "."])
def test_b0_4_plugin_validates_strict(target: str, env: dict[str, str]) -> None:
    result = run([_claude(env), "plugin", "validate", target, "--strict"], env, timeout=120)
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    assert "Validation passed" in result.stdout
