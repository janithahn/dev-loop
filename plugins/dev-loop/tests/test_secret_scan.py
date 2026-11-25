"""`dl ci secret-scan`: token shapes in tracked files are named by path:line, never echoed."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dev_loop.ci import secret_scan, steps

# Samples are built at run time so the tree itself stays clean for the scan.
LONG = "Ab1" * 14
HITS = {
    "github token": "ghp_" + "a1B2" * 9,
    "github fine-grained token": "github_pat_" + "x" * 30,
    "grafana token": "glc_" + LONG,
    "slack token": "xox" + "b-" + "1234567890-abcdefABCDEF",
    "aws access key": "AKIA" + "ABCDEFGHIJKLMNOP",
    "private key block": "-----BEGIN " + "RSA PRIVATE KEY-----",
    "basic auth header": "Authorization: " + "Basic " + LONG,
    "password literal": 'password="' + LONG + '"',
    "token literal": "token=" + LONG,
}
MISSES = [
    'token="$API_TOKEN"',
    "password=short",
    "ghp_tooshort",
    "Authorization: Basic $(cat key)",
    "the AKIA prefix alone",
]


def _repo(root: Path, text: str) -> Path:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "conf.txt").write_text(text, encoding="utf-8")
    (root / "untracked.txt").write_text(HITS["github token"], encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "conf.txt"], check=True)
    return root


@pytest.mark.parametrize(("name", "sample"), HITS.items())
def test_each_shape_is_found_by_line_and_name_never_echoed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str, sample: str,
) -> None:
    root = _repo(tmp_path, f"first line\nsetting {sample} here\n")
    assert secret_scan.scan(root) == 1
    out = capsys.readouterr().out
    assert out.strip() == f"conf.txt:2: {name}"
    secret = sample.split()[-1].split("=")[-1].strip('"')
    assert secret not in out


def test_misses_and_untracked_files_are_clean(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    root = _repo(tmp_path, "\n".join(MISSES) + "\n")
    assert secret_scan.scan(root) == 0
    assert "untracked" not in capsys.readouterr().out


def test_an_ignore_comment_allows_the_line(tmp_path: Path) -> None:
    root = _repo(tmp_path, f"{HITS['aws access key']}  # secret-scan: ignore\n")
    assert secret_scan.scan(root) == 0


def test_dl_ci_runs_the_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _repo(tmp_path, HITS["slack token"] + "\n")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    assert steps.main(["secret-scan"]) == 1
    (root / "conf.txt").write_text("clean\n", encoding="utf-8")
    assert steps.main(["secret-scan"]) == 0


def test_the_plugin_tree_is_clean() -> None:
    assert secret_scan.scan(Path(__file__).resolve().parents[3]) == 0
