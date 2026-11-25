"""`dl config set <dotted> <value>`: one key written through the config writer, validated."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml
from dev_loop import config

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    return tmp_path


def test_set_writes_one_key_as_a_typed_value(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert config.main(["set", "size.cap", "300"]) == 0
    assert yaml.safe_load((root / "dev-loop.yaml").read_text()) == {"size": {"cap": 300}}
    assert "size.cap" in capsys.readouterr().out
    assert config.main(["get", "size.cap"]) == 0
    assert capsys.readouterr().out.strip() == "300"


def test_set_refuses_a_bad_value_with_the_schema_line(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert config.main(["set", "size.cap", "5"]) == 1
    assert "size.cap: a whole number in 50..1000" in capsys.readouterr().err
    assert not (root / "dev-loop.yaml").exists()


def test_set_refuses_an_unknown_key(root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert config.main(["set", "nope.key", "1"]) == 1
    assert "nope.key: unknown key" in capsys.readouterr().err
    assert not (root / "dev-loop.yaml").exists()
