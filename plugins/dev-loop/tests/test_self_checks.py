"""The dashboard's and the context gate's self-checks, each run as its own script.

The scanner, spend and the gate, the owner state and workers, and the server live in
`src/dev_loop/dashboard/`. The scripts below test them on fixture transcripts
(`test_dev_loop_fixtures.py`); each prints "<n> checks, ok" when every check passed.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


@pytest.mark.parametrize(
    "script",
    [
        "test_dev_loop_dashboard.py",
        "test_dev_loop_status.py",
        "test_dev_loop_gate.py",
        "test_dev_loop_store.py",
        "test_dev_loop_workers.py",
        "test_dev_loop_server.py",
        "test_dev_loop_hook.py",
        "test_dev_loop_page.py",
        "test_dev_loop_budgets.py",
        "test_dev_loop_config_roles.py",
        "test_dev_loop_settings.py",
        "test_dev_loop_context_window.py",
        "test_dev_loop_context_errors.py",
    ],
)
def test_dev_loop_dashboard_self_check(script: str) -> None:
    result = subprocess.run(  # noqa: S603 - our own self-check scripts
        [sys.executable, str(HERE / script)],
        cwd=HERE,
        env={**os.environ, "PYTHONPATH": str(HERE.parent / "src")},
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        stdin=subprocess.DEVNULL,  # on the CI agent stdin is an open pipe
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().endswith(" checks, ok"), result.stdout
    assert ": 0 checks" not in result.stdout, result.stdout
