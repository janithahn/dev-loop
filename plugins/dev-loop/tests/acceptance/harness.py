"""The acceptance harness: an isolated user per test and the fixtures F-HUB, F-LOCAL, F-CHG.

Each test gets its own HOME, CLAUDE_CONFIG_DIR, plugin data dir and global git config, and runs
`dl` through the plugin's launcher with a timeout, so nothing touches the real user or project.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
TIMEOUT = 120


class Run:
    """One isolated user: HOME, CLAUDE_CONFIG_DIR, plugin data and a global git config."""

    def __init__(self, base: Path) -> None:
        self.base = base
        home = base / "home"
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("CLAUDE_", "GIT_")) and k != "DEV_LOOP_HOST_TOKEN"}
        self.env |= {
            "HOME": str(home), "USERPROFILE": str(home),
            "CLAUDE_CONFIG_DIR": str(home / ".claude"), "CLAUDE_PLUGIN_DATA": str(base / "data"),
            "GIT_CONFIG_GLOBAL": str(base / "gitconfig"), "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never", "DL_PYTHON": sys.executable,
        }
        (home / ".claude").mkdir(parents=True)
        (base / "data").mkdir()
        for key, value in (("user.email", "noreply@example.invalid"), ("user.name", "tester"),
                           ("init.defaultBranch", "main")):
            self.sh(base, "git", "config", "--global", key, value)

    def sh(self, cwd: Path, *argv: str, stdin: str | None = None,
           check: bool = True) -> subprocess.CompletedProcess[str]:
        done = subprocess.run(argv, cwd=cwd, env=self.env, input=stdin, capture_output=True,
                              text=True, timeout=TIMEOUT, check=False)
        if check and done.returncode:
            raise AssertionError(f"{argv}: {done.returncode}\n{done.stdout}\n{done.stderr}")
        return done

    def dl(self, cwd: Path, *argv: str) -> subprocess.CompletedProcess[str]:
        return self.sh(cwd, "bash", str(PLUGIN / "bin" / "dl"), *argv, check=False)

    def git(self, cwd: Path, *argv: str) -> str:
        return self.sh(cwd, "git", *argv).stdout.strip()


def f_hub(user: Run, remote: str = "origin") -> Path:
    """F-HUB: a bare repo as the hub, cloned, one commit pushed (F-HUB2: remote named hub)."""
    hub, proj = user.base / "hub.git", user.base / "proj"
    user.git(user.base, "init", "-q", "--bare", str(hub))
    user.git(user.base, "clone", "-q", "-o", remote, str(hub), str(proj))
    (proj / "README.md").write_text("demo\n", encoding="utf-8")
    user.git(proj, "add", "README.md")
    user.git(proj, "commit", "-q", "-m", "first")
    user.git(proj, "push", "-q", remote, "main")
    return proj


def f_local(user: Run) -> Path:
    """F-LOCAL: F-HUB, then `dl init --host local` with verify.steps {unit: "true"}, pushed."""
    proj = f_hub(user)
    assert user.dl(proj, "init", "--host", "local").returncode == 0
    assert user.dl(proj, "config", "set", "verify.steps", '{unit: "true"}').returncode == 0
    user.git(proj, "add", "-A")
    user.git(proj, "commit", "-q", "-m", "dl init")
    user.git(proj, "push", "-q", "origin", "main")
    return proj


def f_chg(user: Run) -> tuple[Path, Path]:
    """F-CHG: F-LOCAL, then `dl propose demo`, a file added, every task ticked, committed."""
    proj = f_local(user)
    done = user.dl(proj, "propose", "demo")
    assert done.returncode == 0, done.stderr
    work = Path(done.stdout.strip().splitlines()[-1])
    (work / "demo.txt").write_text("demo\n", encoding="utf-8")
    tasks = work / ".dev-loop" / "changes" / "demo" / "tasks.md"
    if tasks.exists():
        tasks.write_text(tasks.read_text(encoding="utf-8").replace("- [ ]", "- [x]"), "utf-8")
    user.git(work, "add", "-A")
    user.git(work, "commit", "-q", "-m", "demo")
    return proj, work


def clean_run(user: Run, work: Path) -> None:
    """V23: a round needs a clean review run; no PR here, a subagent stands in."""
    assert user.dl(work, "config", "set", "review.fallback", "subagent").returncode == 0


def files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*")
            if p.is_file() and ".git" not in p.relative_to(root).parts}
