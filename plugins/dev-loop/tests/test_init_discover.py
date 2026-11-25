"""dl init discover: finds the project's docs and sets knowledge.read_first and guides once."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml
from dev_loop import init, init_discover
from dev_loop.config import load
from test_neutral import check

PLUGIN = Path(__file__).resolve().parents[1]
DOCS = [
    "CLAUDE.md", "AGENTS.md", "ARCHITECTURE.md", "CONTRIBUTING.md", "README.md",
    "CODE-CONVENTIONS.md", "docs/plans/conventions.md", "docs/adr/0001-stack.md",
    "api/CLAUDE.md", "web/src/AGENTS.md", "node_modules/x/CLAUDE.md", "build/CLAUDE.md",
]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    for name in DOCS:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("x\n", encoding="utf-8")
    return tmp_path


def test_discover_finds_root_docs_conventions_adr_and_area_guides(repo: Path) -> None:
    found = init_discover.discover(repo)
    assert found["read_first"] == [
        "CLAUDE.md", "AGENTS.md", "ARCHITECTURE.md", "CONTRIBUTING.md",
        "CODE-CONVENTIONS.md", "docs/plans/conventions.md", "docs/adr/",
    ]
    assert found["guides"] == ["api/CLAUDE.md", "web/src/AGENTS.md"]


def test_templates_folders_are_not_guides(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    for name in ("CLAUDE.md", "templates/CLAUDE.md", "plugin/templates/AGENTS.md"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("x\n", encoding="utf-8")
    found = init_discover.discover(tmp_path)
    assert found["read_first"] == ["CLAUDE.md"]
    assert found["guides"] == []


def test_writes_both_keys_once_and_keeps_the_template(repo: Path) -> None:
    init.init(repo)
    before = (repo / "dev-loop.yaml").read_text(encoding="utf-8")
    assert init_discover.apply(repo) == ["knowledge.read_first", "knowledge.guides"]
    after = (repo / "dev-loop.yaml").read_text(encoding="utf-8")
    assert after.startswith(before)  # the template's comments survive
    loaded = load(repo)
    assert loaded.error is None
    assert loaded.get("knowledge.guides") == ["api/CLAUDE.md", "web/src/AGENTS.md"]
    (repo / "lib").mkdir()
    (repo / "lib" / "CLAUDE.md").write_text("x\n", encoding="utf-8")
    assert init_discover.apply(repo) == []
    assert (repo / "dev-loop.yaml").read_text(encoding="utf-8") == after


def test_never_overwrites_a_set_key(repo: Path) -> None:
    (repo / "dev-loop.yaml").write_text(
        "version: 1\nknowledge:\n  read_first: [mine.md]\n  pitfalls: P.md\n", encoding="utf-8"
    )
    assert init_discover.apply(repo) == ["knowledge.guides"]
    knowledge = yaml.safe_load((repo / "dev-loop.yaml").read_text(encoding="utf-8"))["knowledge"]
    assert knowledge == {
        "read_first": ["mine.md"], "pitfalls": "P.md",
        "guides": ["api/CLAUDE.md", "web/src/AGENTS.md"],
    }


def test_an_empty_list_set_by_hand_counts_as_set(repo: Path) -> None:
    (repo / "dev-loop.yaml").write_text(
        "version: 1\nknowledge:\n  read_first: []\n  guides: []\n", encoding="utf-8"
    )
    assert init_discover.apply(repo) == []


def test_nothing_found_writes_nothing(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "dev-loop.yaml").write_text("version: 1\n", encoding="utf-8")
    assert init_discover.apply(tmp_path) == []
    assert (tmp_path / "dev-loop.yaml").read_text(encoding="utf-8") == "version: 1\n"


def test_flow_style_knowledge_is_refused_untouched(repo: Path) -> None:
    text = "version: 1\nknowledge: {pitfalls: P.md}\n"
    (repo / "dev-loop.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="knowledge"):
        init_discover.apply(repo)
    assert (repo / "dev-loop.yaml").read_text(encoding="utf-8") == text


def test_dl_init_discover_dispatches(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
    (repo / "dev-loop.yaml").write_text("version: 1\n", encoding="utf-8")
    assert init.main(["discover"]) == 0
    assert load(repo).sources["knowledge.read_first"] == "project"


def test_init_skill_runs_init_discovery_then_doctor() -> None:
    text = (PLUGIN / "skills/init/SKILL.md").read_text(encoding="utf-8")
    meta = yaml.safe_load(text.split("---\n")[1])
    assert meta["name"] == "init"
    assert meta["description"]
    steps = [text.index(step) for step in ("dl init\n", "dl init discover", "dl doctor")]
    assert steps == sorted(steps)


def test_written_files_are_neutral() -> None:
    root = PLUGIN.parents[1]
    files = [
        "plugins/dev-loop/src/dev_loop/init_discover.py", "plugins/dev-loop/src/dev_loop/init.py",
        "plugins/dev-loop/skills/init/SKILL.md", "plugins/dev-loop/tests/test_init_discover.py",
    ]
    assert check(files, root) == []
