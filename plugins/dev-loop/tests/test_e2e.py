"""The whole v0.1 flow through `bin/dl` on a temporary git repository.

The origin is Azure DevOps-shaped (`dl init` reads org, project and repo from it); git reaches a
local bare repository through `url.<path>.insteadOf`, given only to the steps that fetch or push,
since `git remote get-url` would show the rewrite. The host is a local stub (DEV_LOOP_HOST_URL).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import yaml

DL = Path(__file__).resolve().parents[1] / "bin" / "dl"
ORIGIN = "https://dev.azure.com/acme/proj/_git/repo"
SEEN: list[tuple[str, bool]] = []  # (path, carried an Authorization header)


class Host(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - the http.server name
        SEEN.append((self.path, "Authorization" in self.headers))
        body = json.dumps({"value": []}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_: object) -> None:
        pass


@pytest.fixture
def host() -> str:
    server = HTTPServer(("127.0.0.1", 0), Host)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    SEEN.clear()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def approve(tree: Path, env: dict[str, str], slug: str) -> None:
    """The person's `Approved: <slug>` message, through the hook that alone writes the record."""
    hook = DL.parents[1] / "hooks" / "approval.py"
    event = {"hook_event_name": "UserPromptSubmit", "prompt": f"Approved: {slug}", "cwd": str(tree)}
    done = subprocess.run([sys.executable, str(hook)], input=json.dumps(event), env=env,
                          capture_output=True, text=True, check=False, cwd=tree)  # noqa: S603
    assert done.returncode == 0 and not done.stderr, done.stderr
    spec = tree / ".dev-loop" / "changes" / slug / "spec.md"
    if not spec.exists():  # V1: `dl pr` needs a done-when bullet under the acceptance heading
        spec.write_text("## Acceptance criteria\n- `dl x` prints y\n", encoding="utf-8")


def git(cwd: Path, *args: str, env: dict[str, str]) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=env,
                          check=False)
    assert done.returncode == 0, (args, done.stderr)
    return done.stdout.strip()


def dl(cwd: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(DL), *args], cwd=cwd, capture_output=True, text=True,
                          env={**env, "CLAUDE_PROJECT_DIR": str(cwd)}, check=False)


@pytest.fixture
def env(tmp_path: Path, host: str) -> dict[str, str]:
    """No real credential helper or user config; the token in the configured variable."""
    (tmp_path / "gitconfig").write_text("", encoding="utf-8")
    drop = ("CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_ROOT", "CLAUDE_PLUGIN_DATA", "GIT_DIR")
    base = {k: v for k, v in os.environ.items() if k not in drop and not k.startswith("GIT_CONFIG")}
    return base | {
        "GIT_CONFIG_GLOBAL": str(tmp_path / "gitconfig"), "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0", "GIT_AUTHOR_NAME": "t", "GIT_COMMITTER_NAME": "t",
        "GIT_AUTHOR_EMAIL": "noreply@example.com", "GIT_COMMITTER_EMAIL": "noreply@example.com",
        "DL_PYTHON": sys.executable, "DEV_LOOP_HOST_URL": host,
        "DEV_LOOP_HOST_TOKEN": "e2e-token",
    }


def reach(env: dict[str, str], bare: Path) -> dict[str, str]:
    """The env with the ADO origin rewritten to the local bare repository."""
    rewrite = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": f"url.{bare}.insteadOf",
               "GIT_CONFIG_VALUE_0": ORIGIN}
    return env | rewrite


@pytest.fixture
def work(tmp_path: Path, env: dict[str, str]) -> Path:
    bare, work = tmp_path / "origin.git", tmp_path / "work"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare), env=env)
    git(tmp_path, "clone", "-q", str(bare), str(work), env=env)
    (work / "README.md").write_text("# app\n", encoding="utf-8")
    git(work, "add", "-A", env=env)
    git(work, "commit", "-qm", "base", env=env)
    git(work, "push", "-q", "origin", "HEAD:main", env=env)
    git(work, "remote", "set-url", "origin", ORIGIN, env=env)
    git(work, "remote", "set-head", "origin", "main", env=env)
    return work


def test_the_whole_flow(work: Path, env: dict[str, str]) -> None:
    online = reach(env, work.parent / "origin.git")
    # init: the config from the origin, then the project commits it with its verify step
    assert dl(work, env, "init").returncode == 0
    config = yaml.safe_load((work / "dev-loop.yaml").read_text(encoding="utf-8"))
    assert {k: config["host"][k] for k in ("org", "project", "repo")} == {
        "org": "acme", "project": "proj", "repo": "repo"}
    assert config["branches"]["integration"] == "main"
    assert git(work, "config", "core.hooksPath", env=env) == ".githooks"
    git(work, "config", "--unset", "core.hooksPath", env=env)  # the githooks have their own tests
    with (work / "dev-loop.yaml").open("a", encoding="utf-8") as file:
        file.write("verify:\n  steps: {unit: 'test -f src/search.py'}\n")
    git(work, "add", "-A", env=env)
    git(work, "commit", "-qm", "dev loop", env=env)
    git(work, "push", "-q", "origin", "HEAD:main", env=online)

    # doctor: every row green against the stubbed host, the token never printed
    hooked = env | {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.hooksPath",
                    "GIT_CONFIG_VALUE_0": ".githooks"}  # as init set it, for doctor alone
    doctor = dl(work, hooked, "doctor")
    assert doctor.returncode == 0, doctor.stdout + doctor.stderr
    assert "e2e-token" not in doctor.stdout + doctor.stderr
    assert SEEN == [("/acme/proj/_apis/git/repositories?api-version=7.1", True)]

    # propose: a worktree on the change branch holding the change folder
    proposed = dl(work, online, "propose", "add-search", "--title", "Add search")
    assert proposed.returncode == 0, proposed.stderr
    tree = Path(proposed.stdout.strip())
    prefix, changes = config["branches"]["change_prefix"], config["paths"]["changes"]
    assert git(tree, "branch", "--show-current", env=env) == f"{prefix}add-search"
    folder = tree / changes / "add-search"
    assert (folder / "proposal.md").is_file() and (folder / "status.md").is_file()

    # record and tasklist: a record line, then the plan's task list parsed
    assert dl(tree, env, "record", "add-search", "skills", "skills=tdd").returncode == 0
    assert "skills" in (folder / "records.jsonl").read_text(encoding="utf-8")
    task = "### T1: Search\n- Status: done\n- Kind: backend\n- Files: src/search.py\nAdd it.\n"
    (folder / "tasks.md").write_text(f"# Tasks: Add search\n\n## Tasks\n\n{task}", "utf-8")
    parsed = dl(tree, env, "tasklist", "parse", "add-search")
    assert parsed.returncode == 0, parsed.stderr
    assert [(t["id"], t["status"]) for t in json.loads(parsed.stdout)] == [("T1", "done")]

    # build, size cap, verify
    (tree / "src").mkdir()
    (tree / "src" / "search.py").write_text("def search() -> None:\n    pass\n", "utf-8")
    git(tree, "add", "-A", env=env)
    git(tree, "commit", "-qm", "add search", env=env)
    capped = dl(tree, env, "ci", "size-cap")
    assert capped.returncode == 0, capped.stdout + capped.stderr
    verified = dl(tree, env, "verify", "add-search")
    assert verified.returncode == 0, verified.stdout + verified.stderr
    assert "passed" in (folder / "verify-report.md").read_text(encoding="utf-8")

    # merge, then archive on the integration branch
    git(tree, "add", "-A", env=env)
    git(tree, "commit", "-qm", "verify record", env=env)
    git(work, "merge", "-q", "--no-ff", "-m", "merge", f"{prefix}add-search", env=env)
    git(work, "push", "-q", "origin", "HEAD:main", env=online)  # archive reads the remote's main
    archived = dl(tree, online, "archive", "add-search")  # from the change worktree
    assert archived.returncode == 0, archived.stderr
    moved = Path(archived.stdout.strip())
    assert moved.parent == tree / config["paths"]["archive"]
    assert moved.name.endswith("-add-search") and (moved / "verify-report.md").is_file()
    assert not (tree / changes / "add-search").exists()
