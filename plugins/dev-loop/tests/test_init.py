"""dl init: files in a temp repo with an Azure DevOps origin; a second run changes nothing."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml
from dev_loop import init
from test_neutral import check

URL = "https://org1" + "@dev.azure.com/org1/My%20Proj/_git/repo1"
IDENT = ["-c", "user.name=t", "-c", "user.email=noreply@example.com"]


def run(root: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True)
    return done.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    run(tmp_path, "init", "-q")
    run(tmp_path, "remote", "add", "origin", URL)
    return tmp_path


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and ".git/" not in p.as_posix()
    }


def test_writes_everything_then_nothing(repo: Path) -> None:
    (repo / ".claude").mkdir()
    (repo / ".claude" / "settings.json").write_text('{"model": "x", "enabledPlugins": {"a@b": 1}}')
    assert "dev-loop.yaml" in init.init(repo)
    first = snapshot(repo)
    assert init.init(repo) == []
    assert snapshot(repo) == first

    host = yaml.safe_load((repo / "dev-loop.yaml").read_text())["host"]
    assert (host["org"], host["project"], host["repo"]) == ("org1", "My Proj", "repo1")
    settings = json.loads((repo / ".claude" / "settings.json").read_text())
    assert settings["model"] == "x"
    assert settings["enabledPlugins"] == {"a@b": 1, "dev-loop@dev-loop": True}
    market = settings["extraKnownMarketplaces"]["dev-loop"]
    assert market["autoUpdate"] is False
    assert market["source"]["ref"].startswith("v")
    hook = (repo / ".githooks" / "pre-push").read_text()
    assert hook == init.GITHOOK.replace("{name}", "pre-push")
    assert run(repo, "config", "core.hooksPath") == ".githooks"
    assert (repo / "REVIEW-PITFALLS.md").exists()


def test_existing_files_survive(repo: Path) -> None:
    (repo / "dev-loop.yaml").write_text("version: 1\n")
    (repo / "DECISIONS.md").write_text("mine\n")
    init.init(repo)
    assert (repo / "dev-loop.yaml").read_text() == "version: 1\n"
    assert (repo / "DECISIONS.md").read_text() == "mine\n"


def test_integration_from_origin_head(repo: Path) -> None:
    run(repo, *IDENT, "commit", "-q", "--allow-empty", "-m", "x")
    run(repo, "update-ref", "refs/remotes/origin/trunk", "HEAD")
    run(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
    init.init(repo)
    config = yaml.safe_load((repo / "dev-loop.yaml").read_text())
    assert config["branches"]["integration"] == "trunk"



IMPORT = "@.dev-loop/RULES.md"


def test_rules_written_and_claude_md_created(repo: Path) -> None:
    changed = init.init(repo)
    assert ".dev-loop/RULES.md" in changed
    assert "CLAUDE.md" in changed
    rules = (repo / ".dev-loop" / "RULES.md").read_text()
    assert rules == (init.TEMPLATES / "RULES.md").read_text()
    assert "{{" not in rules
    agents = (repo / "AGENTS.md").read_text()
    assert agents.startswith("# repo1 engineering guide")  # named from the origin, not tmp_path
    assert "{{" not in agents  # every placeholder is filled
    assert init.POINTER in agents.splitlines()
    assert (repo / "CLAUDE.md").read_text() == f"@AGENTS.md\n{IMPORT}\n"


def test_existing_claude_md_gains_only_the_import(repo: Path) -> None:
    mine = b"# Mine\r\n\nkeep \xc3\xa9 these bytes"
    (repo / "CLAUDE.md").write_bytes(mine)
    assert "CLAUDE.md" in init.init(repo)
    assert (repo / "CLAUDE.md").read_bytes() == mine + b"\n\n" + IMPORT.encode() + b"\n"
    first = snapshot(repo)
    assert init.init(repo) == []
    assert snapshot(repo) == first


def test_existing_import_left_alone(repo: Path) -> None:
    mine = f"# Mine\n{IMPORT}\nmore\n"
    (repo / "CLAUDE.md").write_text(mine)
    assert "CLAUDE.md" not in init.init(repo)
    assert (repo / "CLAUDE.md").read_text() == mine


def test_stale_rules_refreshed(repo: Path) -> None:
    init.init(repo)
    (repo / ".dev-loop" / "RULES.md").write_text("old rules\n")
    assert init.init(repo) == [".dev-loop/RULES.md"]
    rules = (init.TEMPLATES / "RULES.md").read_text()
    assert (repo / ".dev-loop" / "RULES.md").read_text() == rules


def test_rules_template_uses_dl_and_names_real_agents() -> None:
    import re

    rules = (init.TEMPLATES / "RULES.md").read_text()
    assert "`loop " not in rules
    assert "Managed by the dev-loop plugin; `dl init` refreshes it." in rules
    agents = init.PLUGIN / "agents"
    for name in re.findall(r"`dev-loop:([a-z-]+)`", rules):
        assert (agents / f"{name}.md").is_file(), name
    claude = (init.TEMPLATES / "CLAUDE.md").read_text()
    assert IMPORT in claude.splitlines()
    assert "## Size limits" not in claude
    agents = (init.TEMPLATES / "AGENTS.md").read_text()
    assert init.POINTER in agents.splitlines()
    assert "## Size limits" not in agents

def test_ssh_url() -> None:
    found = init.ADO.search("git" + "@ssh.dev.azure.com:v3/o/p/r")
    assert found
    assert found.groupdict() == {"org": "o", "project": "p", "repo": "r"}


def test_written_files_are_neutral() -> None:
    root = Path(__file__).resolve().parents[3]
    assert check(["plugins/dev-loop/src/dev_loop/init.py", __file__], root) == []


GITHUB_URLS = ["https://github.com/own1/repo1", "https://github.com/own1/repo1.git",
               "git" + "@github.com:own1/repo1.git"]
WORKFLOW = ".github/workflows/dev-loop.yml"


@pytest.mark.parametrize("url", GITHUB_URLS)
def test_github_origin_writes_host_and_workflow(tmp_path: Path, url: str) -> None:
    run(tmp_path, "init", "-q")
    run(tmp_path, "remote", "add", "origin", url)
    changed = init.init(tmp_path)
    assert WORKFLOW in changed
    assert "azure/dev-loop-steps.yml" not in changed
    host = yaml.safe_load((tmp_path / "dev-loop.yaml").read_text())["host"]
    assert (host["provider"], host["org"], host["project"], host["repo"]) == (
        "github", "own1", None, "repo1")
    flow = (tmp_path / WORKFLOW).read_text()
    assert "{{tag}}" not in flow
    assert f'DEV_LOOP_TAG: "v{init.version()}"' in flow
    first = snapshot(tmp_path)
    assert init.init(tmp_path) == []
    assert snapshot(tmp_path) == first


def test_existing_workflow_survives(tmp_path: Path) -> None:
    run(tmp_path, "init", "-q")
    run(tmp_path, "remote", "add", "origin", GITHUB_URLS[0])
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / WORKFLOW).write_text("mine\n")
    assert WORKFLOW not in init.init(tmp_path)
    assert (tmp_path / WORKFLOW).read_text() == "mine\n"


def test_ado_origin_gets_the_azure_template_only(repo: Path) -> None:
    changed = init.init(repo)
    assert "azure/dev-loop-steps.yml" in changed
    assert not (repo / ".github").exists()
    assert yaml.safe_load((repo / "dev-loop.yaml").read_text())["host"]["provider"] == "ado"


def test_host_flag_forces_the_provider(tmp_path: Path) -> None:
    run(tmp_path, "init", "-q")
    run(tmp_path, "remote", "add", "origin", "https://example.invalid/own1/repo1.git")
    init.init(tmp_path, host="github")
    host = yaml.safe_load((tmp_path / "dev-loop.yaml").read_text())["host"]
    assert (host["provider"], host["org"], host["repo"]) == ("github", None, None)
    assert (tmp_path / WORKFLOW).exists()


def test_host_flag_local_writes_no_ci(tmp_path: Path) -> None:
    run(tmp_path, "init", "-q")
    run(tmp_path, "remote", "add", "origin", "https://example.invalid/own1/repo1.git")
    changed = init.init(tmp_path, host="local")
    host = yaml.safe_load((tmp_path / "dev-loop.yaml").read_text())["host"]
    assert host["provider"] == "local"
    assert not any("azure" in item or ".github" in item for item in changed)
    assert not (tmp_path / "azure").exists() and not (tmp_path / ".github").exists()


def test_host_flag_on_the_command_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    run(tmp_path, "init", "-q")
    run(tmp_path, "remote", "add", "origin", GITHUB_URLS[0])
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    assert init.main(["--host", "ado"]) == 0
    assert "azure/dev-loop-steps.yml" in capsys.readouterr().out
    host = yaml.safe_load((tmp_path / "dev-loop.yaml").read_text())["host"]
    assert (host["provider"], host["org"]) == ("ado", None)
    with pytest.raises(SystemExit):
        init.main(["--host", "other"])


# B2: AGENTS.md is canonical for other harnesses; each combination of the two files.
POINTER_LINE = "Follow the working rules in [.dev-loop/RULES.md](.dev-loop/RULES.md)."


def test_pointer_line_is_plain_markdown() -> None:
    assert init.POINTER == POINTER_LINE


def test_neither_file_writes_both(repo: Path) -> None:
    changed = init.init(repo)
    assert {"AGENTS.md", "CLAUDE.md"} <= set(changed)
    assert (repo / "CLAUDE.md").read_bytes() == b"@AGENTS.md\n@.dev-loop/RULES.md\n"
    agents = (repo / "AGENTS.md").read_text()
    assert "{{" not in agents  # every placeholder is filled
    assert POINTER_LINE in agents.splitlines()
    assert IMPORT not in agents.splitlines()
    first = snapshot(repo)
    assert init.init(repo) == []
    assert snapshot(repo) == first


def test_only_claude_md_keeps_its_bytes_and_gets_agents_md(repo: Path) -> None:
    mine = b"# Mine\r\nkeep \xc3\xa9"
    (repo / "CLAUDE.md").write_bytes(mine)
    changed = init.init(repo)
    assert {"AGENTS.md", "CLAUDE.md"} <= set(changed)
    assert (repo / "CLAUDE.md").read_bytes() == mine + b"\n\n" + IMPORT.encode() + b"\n"
    assert POINTER_LINE in (repo / "AGENTS.md").read_text().splitlines()
    first = snapshot(repo)
    assert init.init(repo) == []
    assert snapshot(repo) == first


def test_only_agents_md_gains_the_pointer_and_claude_md_imports_it(repo: Path) -> None:
    mine = b"# Our law\n\n1. keep \xc3\xa9 these bytes"
    (repo / "AGENTS.md").write_bytes(mine)
    changed = init.init(repo)
    assert {"AGENTS.md", "CLAUDE.md"} <= set(changed)
    assert (repo / "AGENTS.md").read_bytes() == mine + b"\n\n" + POINTER_LINE.encode() + b"\n"
    assert (repo / "CLAUDE.md").read_bytes() == b"@AGENTS.md\n@.dev-loop/RULES.md\n"
    first = snapshot(repo)
    assert init.init(repo) == []
    assert snapshot(repo) == first


def test_both_files_each_gain_only_their_missing_line(repo: Path) -> None:
    (repo / "AGENTS.md").write_bytes(b"# Law\n")
    (repo / "CLAUDE.md").write_bytes(f"# Mine\n{IMPORT}\n".encode())
    changed = init.init(repo)
    assert "AGENTS.md" in changed
    assert "CLAUDE.md" not in changed
    assert (repo / "AGENTS.md").read_bytes() == b"# Law\n\n" + POINTER_LINE.encode() + b"\n"
    assert (repo / "CLAUDE.md").read_bytes() == f"# Mine\n{IMPORT}\n".encode()
    first = snapshot(repo)
    assert init.init(repo) == []
    assert snapshot(repo) == first


def test_both_files_already_wired_change_nothing(repo: Path) -> None:
    (repo / "AGENTS.md").write_text(f"# Law\n{POINTER_LINE}\n")
    (repo / "CLAUDE.md").write_text(f"# Mine\n{IMPORT}\n")
    changed = init.init(repo)
    assert "AGENTS.md" not in changed
    assert "CLAUDE.md" not in changed
