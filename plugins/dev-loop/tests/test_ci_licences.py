"""`dl ci licences`: each direct dependency's licence against licences.allow; unknown is red."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_neutral import check

from dev_loop.ci import licences


def dist(site: Path, name: str, *lines: str) -> None:
    """Write a fake installed distribution's METADATA under a site-packages path."""
    info = site / f"{name}-1.0.dist-info"
    info.mkdir(parents=True)
    head = f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n"
    (info / "METADATA").write_text(head + "".join(f"{line}\n" for line in lines), encoding="utf-8")


def node_pkg(root: Path, name: str, licence: object) -> None:
    """Write node_modules/<name>/package.json with the given `license` value."""
    pkg = root / "node_modules" / name
    pkg.mkdir(parents=True)
    (pkg / "package.json").write_text(json.dumps({"name": name, "license": licence}), "utf-8")


def pyproject(root: Path, *deps: str) -> None:
    body = ", ".join(f'"{dep}"' for dep in deps)
    (root / "pyproject.toml").write_text(f"[project]\ndependencies = [{body}]\n", encoding="utf-8")


def run(root: Path, site: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = licences.check(root, [str(site)])
    return code, capsys.readouterr().out


def test_allowed_dependency_is_ok(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    site = tmp_path / "site"
    dist(site, "goodpkg", "License-Expression: MIT")
    pyproject(tmp_path, "goodpkg>=1.0")
    code, out = run(tmp_path, site, capsys)
    assert code == 0
    assert "goodpkg" in out and "MIT" in out and "ok" in out


def test_denied_licence_is_red(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    site = tmp_path / "site"
    dist(site, "copyleft", "License-Expression: GPL-3.0-only")
    pyproject(tmp_path, "copyleft")
    code, out = run(tmp_path, site, capsys)
    assert code == 1
    assert "copyleft" in out and "GPL-3.0-only" in out and "red" in out


def test_unreadable_licence_is_unknown_and_red(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    site = tmp_path / "site"
    dist(site, "nolicence")  # metadata with no licence field at all
    pyproject(tmp_path, "nolicence", "absent")  # absent is not installed in the site
    code, out = run(tmp_path, site, capsys)
    assert code == 1
    assert out.count("unknown") == 2  # noqa: PLR2004 - both deps resolve to unknown
    assert "red" in out


def test_classifier_maps_to_an_spdx_id(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    site = tmp_path / "site"
    dist(site, "classy", "Classifier: License :: OSI Approved :: Apache Software License")
    pyproject(tmp_path, "classy")
    code, out = run(tmp_path, site, capsys)
    assert code == 0
    assert "Apache-2.0" in out and "ok" in out


def test_requirements_txt_is_read_when_no_pyproject(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    site = tmp_path / "site"
    dist(site, "reqpkg", "License: ISC")
    (tmp_path / "requirements.txt").write_text("# a comment\nreqpkg==2.3  # pinned\n", "utf-8")
    code, out = run(tmp_path, site, capsys)
    assert code == 0
    assert "reqpkg" in out and "ISC" in out and "ok" in out


def test_node_dependency_licence_is_read_from_node_modules(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"left-pad": "^1.0.0", "ghost": "^2.0.0"}}), "utf-8")
    node_pkg(tmp_path, "left-pad", "MIT")  # ghost has no node_modules entry: unknown, red
    code, out = run(tmp_path, tmp_path / "site", capsys)
    assert code == 1
    assert "left-pad" in out and "MIT" in out and "ok" in out
    assert "ghost" in out and "unknown" in out


def test_no_manifest_is_not_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(tmp_path, tmp_path / "site", capsys)
    assert code == 1
    assert out.strip() == "not run: no dependency manifest"


def test_the_new_files_are_neutral() -> None:
    assert check([
        "plugins/dev-loop/src/dev_loop/ci/licences.py",
        "plugins/dev-loop/tests/test_ci_licences.py",
    ]) == []
