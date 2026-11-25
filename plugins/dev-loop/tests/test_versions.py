"""One version everywhere: plugin.json = marketplace.json = pyproject.toml (= the tag, on a tag).

Also the manifest shape CI falls back to when the `claude` CLI cannot be installed, and the CI
workflow's own steps.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from pathlib import Path

import yaml
from test_neutral import check

ROOT = Path(__file__).resolve().parents[3]
PLUGIN = ROOT / "plugins/dev-loop/.claude-plugin/plugin.json"
MARKET = ROOT / ".claude-plugin/marketplace.json"
PYPROJECT = ROOT / "pyproject.toml"
CI = ROOT / ".github/workflows/ci.yml"
RELEASE = "0.4.23"


def normal(version: str) -> str:
    """The PEP 440 spelling of a version: `v0.1.2-dev` and `0.1.2.dev0` are the same release."""
    version = version.removeprefix("v")
    return re.sub(r"[-.]?dev(\d*)$", lambda m: f".dev{m.group(1) or 0}", version)


def versions() -> dict[str, str]:
    market = json.loads(MARKET.read_text())
    entry = next(p for p in market["plugins"] if p["name"] == "dev-loop")
    return {
        "plugin.json": json.loads(PLUGIN.read_text())["version"],
        "marketplace.json": entry["version"],
        "pyproject.toml": tomllib.loads(PYPROJECT.read_text())["project"]["version"],
    }


def test_normal() -> None:
    assert normal("0.1.2-dev") == normal("0.1.2.dev0") == normal("v0.1.2dev") == "0.1.2.dev0"
    assert normal("v1.2.3") == "1.2.3"
    assert normal("0.1.2-dev") != normal("0.1.2")


def test_one_version() -> None:
    found = versions()
    assert len({normal(v) for v in found.values()}) == 1, found


def test_release_version() -> None:
    """v0.1 closes at 0.1.2 in all three files; a later release moves this with them."""
    assert set(versions().values()) == {RELEASE}, versions()


def test_tag_matches_version() -> None:
    if os.environ.get("GITHUB_REF_TYPE") != "tag":
        return
    tag = os.environ["GITHUB_REF_NAME"]
    assert {normal(v) for v in versions().values()} == {normal(tag)}


def test_manifest_shape() -> None:
    plugin = json.loads(PLUGIN.read_text())
    assert plugin["name"] == "dev-loop"
    assert isinstance(plugin.get("description"), str)
    market = json.loads(MARKET.read_text())
    assert isinstance(market["name"], str)
    assert isinstance(market["owner"]["name"], str)
    for entry in market["plugins"]:
        assert (ROOT / entry["source"] / ".claude-plugin/plugin.json").is_file(), entry


def test_ci_workflow() -> None:
    steps = yaml.safe_load(CI.read_text())["jobs"]["test"]["steps"]
    text = "\n".join(str(step.get("run", "")) for step in steps)
    python = next(s for s in steps if "setup-python" in s.get("uses", ""))
    assert python["with"]["python-version"] == "3.12"
    assert "pip install -e ." in text
    assert "pytest plugins/dev-loop/tests" in text
    assert "claude plugin validate plugins/dev-loop --strict" in text
    assert check([CI, Path(__file__)]) == []
