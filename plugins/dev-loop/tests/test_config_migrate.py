"""Config versions (V5): a newer file refused, an older one migrated, the schema frozen per version.

No real migration exists while 1 is the latest: the machinery is proven with a test-only
version 2 whose migration doubles size.cap.

Run: python -m pytest plugins/dev-loop/tests -q -k "migrate"
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from dev_loop import config, config_migrate, config_write, schema

FIXTURES = Path(__file__).resolve().parent / "fixtures"
NEWER = "dev-loop.yaml is version 2; this plugin reads up to 1: update the plugin"


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A project root with its own HOME, so `migrate` never reads the user's settings."""
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    return tmp_path


def shipped() -> dict[str, Any]:
    return yaml.safe_load(config.DEFAULTS.read_text(encoding="utf-8"))


def double_the_cap(old: dict[str, Any]) -> dict[str, Any]:
    """The fake 1 -> 2 migration: version 2 counts size.cap in half-lines."""
    if "cap" not in old.get("size", {}):
        return old
    return old | {"size": old["size"] | {"cap": old["size"]["cap"] * 2}}


@pytest.fixture
def v2(root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A plugin at a fake version 2: defaults at 2, a registered 1 -> 2 migration."""
    defaults = root / "defaults-v2.yaml"
    defaults.write_text(yaml.safe_dump(shipped() | {"version": 2}), encoding="utf-8")
    monkeypatch.setattr(config, "DEFAULTS", defaults)
    monkeypatch.setattr(config_write, "DEFAULTS", defaults)
    monkeypatch.setattr(schema, "SUPPORTED", 2)
    monkeypatch.setattr(config_migrate, "MIGRATIONS", {1: double_the_cap})
    return root


def test_a_newer_file_is_refused_with_one_line(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (root / "dev-loop.yaml").write_text("version: 2\nsize:\n  cap: 300\n", encoding="utf-8")
    loaded = config.load(root)
    assert loaded.error == NEWER, loaded.error
    assert loaded.values == shipped()  # the defaults, so the caps never go off
    with pytest.raises(SystemExit) as refused:
        config.main(["get", "size.cap"])
    assert NEWER in str(refused.value)
    before = (root / "dev-loop.yaml").read_bytes()
    assert config.main(["migrate"]) == 1
    assert NEWER in capsys.readouterr().err
    assert (root / "dev-loop.yaml").read_bytes() == before


@pytest.mark.parametrize("text", ["version: 0\n", "version: two\n", "version: true\n"])
def test_a_bad_version_is_refused_with_its_key(root: Path, text: str) -> None:
    (root / "dev-loop.yaml").write_text(text, encoding="utf-8")
    error = config.load(root).error or ""
    assert "version: a whole number of 1 or more" in error, error


def test_an_older_file_is_migrated_in_memory_with_a_warning(
    v2: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    text = "# my cap\nversion: 1\nsize:\n  cap: 150\n"
    (v2 / "dev-loop.yaml").write_text(text, encoding="utf-8")
    loaded = config.load(v2)
    assert loaded.error is None, loaded.error
    assert loaded.get("size.cap") == 300 and loaded.get("version") == 2
    assert loaded.sources["size.cap"] == "project"
    assert "version 1" in (loaded.warning or "") and "dl config migrate" in (loaded.warning or "")
    assert (v2 / "dev-loop.yaml").read_text(encoding="utf-8") == text  # in memory only
    assert config.main(["get", "size.cap"]) == 0
    out = capsys.readouterr()
    assert out.out.strip() == "300" and "dl config migrate" in out.err


def test_a_file_with_no_version_is_version_1(v2: Path) -> None:
    (v2 / "dev-loop.yaml").write_text("size:\n  cap: 150\n", encoding="utf-8")
    loaded = config.load(v2)
    assert loaded.get("size.cap") == 300 and loaded.warning, loaded


def test_migrate_rewrites_the_file_at_the_latest_version_once(
    v2: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = v2 / "dev-loop.yaml"
    text = "version: 1\nsize:\n  cap: 150\n"
    path.write_text(text, encoding="utf-8")
    assert config.main(["migrate"]) == 0
    said = capsys.readouterr().out
    assert "from version 1 to 2" in said, said
    assert yaml.safe_load(path.read_text(encoding="utf-8")) == {"version": 2, "size": {"cap": 300}}
    assert (v2 / "dev-loop.yaml.bak").read_text(encoding="utf-8") == text
    assert config.load(v2).warning is None
    written = path.read_bytes(), path.stat().st_mtime_ns
    assert config.main(["migrate"]) == 0
    assert "nothing changed" in capsys.readouterr().out
    assert (path.read_bytes(), path.stat().st_mtime_ns) == written


def test_migrate_at_the_latest_version_changes_nothing(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert config.main(["migrate"]) == 0
    assert "no dev-loop.yaml" in capsys.readouterr().out
    path = root / "dev-loop.yaml"
    path.write_text("# kept\nsize:\n  cap: 300\n", encoding="utf-8")
    assert config.main(["migrate"]) == 0
    assert "version 1, the latest; nothing changed" in capsys.readouterr().out
    assert path.read_text(encoding="utf-8") == "# kept\nsize:\n  cap: 300\n"


def shape() -> dict[str, Any]:
    """The schema's sections and their keys (with each plain key's kind)."""
    defaults = shipped()
    keys: dict[str, Any] = {name: dict(kinds) for name, kinds in schema.TEXT.items()}
    keys |= {name: sorted(defaults[name]) for name in ("caps", "handoff", "warnings", "routing")}
    keys |= {"roles": sorted(schema.ROLE_KEYS), "reviewer": sorted({*schema.PROCESS, "tools"})}
    keys |= {"workers": dict(schema.WORKER_KEYS) | schema.WORKER_OPTIONAL, "context": ["default", "max", "min"]}
    return {"version": schema.SUPPORTED, "sections": sorted(schema.SECTIONS), "keys": keys}


def test_the_schema_is_frozen_per_version() -> None:
    """An additive optional key updates the snapshot; a rename, removal or kind change bumps."""
    frozen = FIXTURES / f"schema-v{schema.SUPPORTED}.json"
    told = (
        f"the config schema changed but is still version {schema.SUPPORTED}: raise "
        "schema.SUPPORTED, add the migration to config_migrate.MIGRATIONS and snapshot the new "
        f"schema in tests/fixtures/schema-v{schema.SUPPORTED + 1}.json"
    )
    assert frozen.exists(), told
    assert shape() == json.loads(frozen.read_text(encoding="utf-8")), told
    assert shipped()["version"] == schema.SUPPORTED, "defaults.yaml is not at schema.SUPPORTED"
    missing = set(range(1, schema.SUPPORTED)) - set(config_migrate.MIGRATIONS)
    assert not missing, f"no migration from version {sorted(missing)} in config_migrate.MIGRATIONS"
