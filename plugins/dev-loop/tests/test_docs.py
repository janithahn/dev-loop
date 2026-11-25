"""README, CHANGELOG and ARCHITECTURE name what ships and stay neutral."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml
from dev_loop.__main__ import TABLE
from test_neutral import check

PLUGIN = Path(__file__).resolve().parents[1]
ROOT = PLUGIN.parents[1]
DOCS = ["README.md", "CHANGELOG.md", "ARCHITECTURE.md"]


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_docs_are_neutral() -> None:
    assert check(DOCS) == []


def test_readme_names_every_subcommand_skill_and_config_key() -> None:
    readme = _read("README.md")
    for sub in TABLE:
        assert f"`dl {sub}" in readme, f"dl {sub}"
    for skill in (PLUGIN / "skills").glob("*/SKILL.md"):
        assert f"/dev-loop:{skill.parent.name}`" in readme, skill.parent.name
    defaults = yaml.safe_load((PLUGIN / "src/dev_loop/defaults.yaml").read_text(encoding="utf-8"))
    for key in defaults:
        assert f"`{key}`" in readme, key
    assert "/plugin marketplace add janithahn/dev-loop" in readme
    assert "/dev-loop-settings" in readme


def test_readme_names_no_subcommand_that_is_not_in_the_table() -> None:
    for sub in re.findall(r"`dl ([a-z][a-z-]*)", _read("README.md")):
        assert sub in TABLE, f"dl {sub}"


def test_changelog_has_the_release() -> None:
    assert "## [0.1.0]" in _read("CHANGELOG.md")


def test_architecture_maps_every_hook_and_package() -> None:
    text = _read("ARCHITECTURE.md")
    hooks = json.loads((PLUGIN / "hooks/hooks.json").read_text(encoding="utf-8"))["hooks"]
    for event, groups in hooks.items():
        assert f"`{event}`" in text, event
        for group in groups:
            for hook in group["hooks"]:
                script = re.search(r"/hooks/([\w.-]+)|/bin/([\w.-]+)", hook["command"])
                assert script, hook["command"]
                assert f"`{script.group(1) or script.group(2)}`" in text, hook["command"]
    for package in (PLUGIN / "src/dev_loop").iterdir():
        if package.is_dir() and package.name != "__pycache__":
            assert f"`{package.name}/`" in text, package.name
