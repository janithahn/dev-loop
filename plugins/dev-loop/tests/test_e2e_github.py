"""The whole flow through `bin/dl` on a GitHub-hosted temporary git repository.

The GitHub twin of test_e2e.py. The origin is GitHub-shaped (`dl init` reads owner and repo from
it); git reaches a local bare repository through `url.<path>.insteadOf`, given only to the steps
that fetch or push. The host is a fake `gh` on PATH that logs argv and stdin and answers from a
table: logged in, no open PR, PR 1 opened, review comment 5 in review thread RT_node5.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from test_e2e import DL, approve, dl, git

ORIGIN = "https://github.com/example-owner/example-repo.git"
REPO = "repos/example-owner/example-repo"
THREADS = {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": [
    {"id": "RT_node5", "isResolved": False, "path": "src/search.py", "line": 1,
     "comments": {"nodes": [{"databaseId": 5, "body": "a note"}]}}]}}}}}
REPLIES = [  # (needle in argv + stdin, stdout); the first match answers, else "{}"
    ["auth status", ""],
    ["head=example-owner:", "[]"],
    [f"{REPO}/pulls --method POST", '{"number": 1}'],
    [f"{REPO}/pulls/1 --method GET", json.dumps({
        "number": 1, "title": "Add search", "body": "", "merge_commit_sha": None,
        "head": {"ref": "loop/add-search", "sha": "h1"}, "base": {"ref": "main"}})],
    [f"{REPO}/pulls/1/comments --method POST", '{"id": 5}'],
    ['"query": "query', json.dumps(THREADS)],
]
GH = """#!{python}
import json, os, sys
stdin = sys.stdin.read() if "--input" in sys.argv else ""
folder = os.environ["FAKE_GH"]
with open(os.path.join(folder, "log.jsonl"), "a") as log:
    log.write(json.dumps({{"argv": sys.argv[1:], "stdin": stdin}}) + "\\n")
said = " ".join(sys.argv[1:]) + " " + stdin
for needle, out in json.load(open(os.path.join(folder, "replies.json"))):
    if needle in said:
        sys.stdout.write(out)
        sys.exit(0)
sys.stdout.write("{{}}")
"""


def calls(fake: Path) -> list[dict]:
    lines = (fake / "log.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


@pytest.fixture
def fake(tmp_path: Path) -> Path:
    folder = tmp_path / "gh"
    folder.mkdir()
    script = folder / "gh"
    script.write_text(GH.format(python=sys.executable), encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    (folder / "replies.json").write_text(json.dumps(REPLIES), encoding="utf-8")
    (folder / "log.jsonl").write_text("", encoding="utf-8")
    return folder


@pytest.fixture
def env(tmp_path: Path, fake: Path) -> dict[str, str]:
    """No real credential helper, user config or gh login; the fake gh first on PATH."""
    (tmp_path / "gitconfig").write_text("", encoding="utf-8")
    drop = ("CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_ROOT", "CLAUDE_PLUGIN_DATA", "GIT_DIR",
            "GH_TOKEN", "GITHUB_TOKEN", "DEV_LOOP_HOST_URL", "DEV_LOOP_HOST_TOKEN")
    base = {k: v for k, v in os.environ.items() if k not in drop and not k.startswith("GIT_CONFIG")}
    return base | {
        "GIT_CONFIG_GLOBAL": str(tmp_path / "gitconfig"), "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0", "GIT_AUTHOR_NAME": "t", "GIT_COMMITTER_NAME": "t",
        "GIT_AUTHOR_EMAIL": "noreply@example.com", "GIT_COMMITTER_EMAIL": "noreply@example.com",
        "DL_PYTHON": sys.executable, "FAKE_GH": str(fake),
        "PATH": f"{fake}{os.pathsep}{os.environ.get('PATH', '')}",
    }


def reach(env: dict[str, str], bare: Path) -> dict[str, str]:
    """The env with the GitHub origin rewritten to the local bare repository."""
    return env | {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": f"url.{bare}.insteadOf",
                  "GIT_CONFIG_VALUE_0": ORIGIN}


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


def host(cwd: Path, env: dict[str, str], verb: str, body: dict) -> dict:
    done = subprocess.run(["bash", str(DL), "host", verb, "--json"], cwd=cwd, check=False,
                          input=json.dumps(body), capture_output=True, text=True,
                          env={**env, "CLAUDE_PROJECT_DIR": str(cwd)})
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_the_whole_github_flow(work: Path, env: dict[str, str], fake: Path) -> None:
    online = reach(env, work.parent / "origin.git")
    # init: provider github from the origin, the GitHub workflow, no Azure template
    assert dl(work, env, "init").returncode == 0
    config = yaml.safe_load((work / "dev-loop.yaml").read_text(encoding="utf-8"))
    assert {k: config["host"][k] for k in ("provider", "org", "repo")} == {
        "provider": "github", "org": "example-owner", "repo": "example-repo"}
    assert config["branches"]["integration"] == "main"
    assert (work / ".github" / "workflows" / "dev-loop.yml").is_file()
    assert not (work / "azure").exists()
    git(work, "config", "--unset", "core.hooksPath", env=env)  # the githooks have their own tests
    with (work / "dev-loop.yaml").open("a", encoding="utf-8") as file:
        file.write("verify:\n  steps: {unit: 'test -f src/search.py'}\n")
    git(work, "add", "-A", env=env)
    git(work, "commit", "-qm", "dev loop", env=env)
    git(work, "push", "-q", "origin", "HEAD:main", env=online)

    # doctor: the host rows green through the fake gh's login
    hooked = env | {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.hooksPath",
                    "GIT_CONFIG_VALUE_0": ".githooks"}  # as init set it, for doctor alone
    doctor = dl(work, hooked, "doctor")
    assert doctor.returncode == 0, doctor.stdout + doctor.stderr
    assert "gh logged in" in doctor.stdout
    assert ["auth", "status"] in [call["argv"] for call in calls(fake)]

    # propose, record, tasklist
    proposed = dl(work, online, "propose", "add-search", "--title", "Add search")
    assert proposed.returncode == 0, proposed.stderr
    tree = Path(proposed.stdout.strip())
    approve(tree, env, "add-search")
    prefix, changes = config["branches"]["change_prefix"], config["paths"]["changes"]
    branch = f"{prefix}add-search"
    assert git(tree, "branch", "--show-current", env=env) == branch
    folder = tree / changes / "add-search"
    assert dl(tree, env, "record", "add-search", "skills", "skills=tdd").returncode == 0
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
    simplified = dl(tree, env, "record", "add-search", "simplifier", "edits=none")
    assert simplified.returncode == 0, simplified.stdout + simplified.stderr
    verified = dl(tree, env, "verify", "add-search")
    assert verified.returncode == 0, verified.stdout + verified.stderr
    git(tree, "add", "-A", env=env)
    git(tree, "commit", "-qm", "verify record", env=env)

    # pr: pushed to the bare origin, then `pulls` created with the integration branch as base
    opened = dl(tree, online, "pr", "add-search")
    assert opened.returncode == 0, opened.stdout + opened.stderr
    assert opened.stdout.strip().endswith(": 1")
    assert git(work.parent / "origin.git", "rev-parse", branch, env=env) == git(
        tree, "rev-parse", "HEAD", env=env)
    create = [c for c in calls(fake) if c["argv"][1:4] == [f"{REPO}/pulls", "--method", "POST"]]
    sent = json.loads(create[0]["stdin"])
    assert (sent["head"], sent["base"], sent["title"]) == (branch, "main", "Add search")

    # a review thread on the PR, then resolved
    posted = host(tree, env, "thread-post",
                  {"pr": 1, "file": "/src/search.py", "line": 1, "text": "a note"})
    assert posted == {"id": 5}
    assert json.loads(calls(fake)[-1]["stdin"])["commit_id"] == "h1"
    status = host(tree, env, "thread-status", {"pr": 1, "thread": 5, "status": "fixed"})
    assert status == {"id": 5, "status": "fixed"}
    mutation = json.loads(calls(fake)[-1]["stdin"])
    assert "resolveReviewThread" in mutation["query"]
    assert mutation["variables"] == {"id": "RT_node5"}

    # merge, then archive on the integration branch
    git(work, "merge", "-q", "--no-ff", "-m", "merge", branch, env=env)
    git(work, "push", "-q", "origin", "HEAD:main", env=online)  # archive reads the remote's main
    archived = dl(tree, online, "archive", "add-search")  # from the change worktree
    assert archived.returncode == 0, archived.stderr
    moved = Path(archived.stdout.strip())
    assert moved.parent == tree / config["paths"]["archive"]
    assert (moved / "verify-report.md").is_file()
    assert not (tree / changes / "add-search").exists()
