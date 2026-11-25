"""The harness acceptance batch 1 runs in (test_batch1.py): the brief's preamble and fixtures.

An isolated HOME, CLAUDE_CONFIG_DIR, CLAUDE_PLUGIN_DATA and git config per run; every shell step
under `timeout`; F-HUB and F-LOCAL; a marketplace checkout of this repo at a released version.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
P = ROOT / "plugins/dev-loop"
NOT_RUN = "dl not found, pre-commit checks NOT run"


def claude_bin() -> str | None:
    """The real CLI: a wrapper on PATH may read $HOME, which every run here replaces."""
    real = Path.home() / ".local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe"
    named = os.environ.get("DL_AT_CLAUDE") or (str(real) if real.exists() else None)
    return named or shutil.which("claude")


CLAUDE = claude_bin()
needs_claude = pytest.mark.skipif(CLAUDE is None, reason="no claude CLI")


class Harness:
    """The brief's preamble: an isolated HOME, config, plugin data and git identity."""

    def __init__(self, run: Path) -> None:
        self.run_dir = run
        self.home = run / "home"
        self.data = run / "data"
        self.home.mkdir()
        self.data.mkdir()
        path = [d for d in os.environ["PATH"].split(os.pathsep) if not (Path(d) / "dl").exists()]
        self.env = {
            "HOME": str(self.home), "CLAUDE_CONFIG_DIR": str(self.home / ".claude"),
            "CLAUDE_PLUGIN_DATA": str(self.data), "GIT_CONFIG_GLOBAL": str(run / "gitconfig"),
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0", "LANG": "C.UTF-8",
            "PATH": os.pathsep.join(path),
            "DL_PYTHON": sys.executable, "TMPDIR": str(run),
        }
        for key, value in (("user.email", "noreply@example.com"), ("user.name", "tester"),
                           ("init.defaultBranch", "main")):
            self.sh(["git", "config", "--global", key, value], run)

    def sh(self, cmd: list[str], cwd: Path, limit: int = 120,
           env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        """One shell step under `timeout`; 124 means it hung."""
        return subprocess.run(["timeout", str(limit), *cmd], cwd=cwd, env=env or self.env,
                              capture_output=True, text=True, check=False, timeout=limit + 30)

    def dl(self, cwd: Path, *args: str, plugin: Path = P) -> subprocess.CompletedProcess[str]:
        return self.sh(["bash", str(plugin / "bin/dl"), *args], cwd)

    def claude(self, *args: str) -> subprocess.CompletedProcess[str]:
        return self.sh([str(CLAUDE), *args], self.run_dir, limit=180)

    def hub(self) -> Path:
        """F-HUB: a bare hub, cloned to `proj`, one commit pushed."""
        self.sh(["git", "init", "-q", "--bare", "hub.git"], self.run_dir)
        self.sh(["git", "clone", "-q", "hub.git", "proj"], self.run_dir)
        proj = self.run_dir / "proj"
        (proj / "README.md").write_text("demo\n")
        for step in (["add", "-A"], ["commit", "-qm", "base"], ["push", "-q", "origin", "main"]):
            assert self.sh(["git", *step], proj).returncode == 0, step
        return proj

    def local(self, plugin: Path = P) -> Path:
        """F-LOCAL: F-HUB, then `dl init --host local` with verify.steps {unit: "true"}."""
        proj = self.hub()
        assert self.dl(proj, "init", "--host", "local", plugin=plugin).returncode == 0
        text = (proj / "dev-loop.yaml").read_text()
        (proj / "dev-loop.yaml").write_text(text.replace("steps: {}", 'steps: {unit: "true"}'))
        self.commit(proj, "init")
        no_hooks = ["git", "-c", "core.hooksPath=/dev/null", "push", "-q", "origin", "main"]
        assert self.sh(no_hooks, proj).returncode == 0
        return proj

    def commit(self, proj: Path, message: str) -> None:
        """Set-up commits skip the project's own hooks (they refuse main by design)."""
        for step in (["add", "-A"], ["-c", "core.hooksPath=/dev/null", "commit", "-qm", message]):
            done = self.sh(["git", *step], proj)
            assert done.returncode == 0, done.stderr

    def cache(self, version: str) -> Path:
        return self.home / ".claude/plugins/cache/dev-loop/dev-loop" / version


def market_at(h: Harness, version: str) -> Path:
    """A marketplace checkout of this repo at the commit that released `version`."""
    path = h.run_dir / "market"
    if not path.exists():
        h.sh(["git", "clone", "-q", "--shared", "--no-checkout", str(ROOT), str(path)], h.run_dir)
    found = h.sh(["git", "log", "--all", "--reverse", "--format=%H", "-S",
                  f'"version": "{version}"', "--", "plugins/dev-loop/.claude-plugin/plugin.json"],
                 path).stdout.split()
    if not found:
        pytest.skip(f"no commit releases {version} in this clone")
    assert h.sh(["git", "checkout", "-q", "--detach", found[0]], path).returncode == 0
    return path
