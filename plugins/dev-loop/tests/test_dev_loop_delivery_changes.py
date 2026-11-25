"""Self-check for the delivery export's change series, run through `fleet_export.main`: which
folders are changes, and what a checkout or a record file that cannot be read leaves standing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest import mock

from test_dev_loop_delivery import Fake, checkout, run, series

from dev_loop.fleet import delivery


def export(tmp_path: Path, **row: Any) -> tuple[int, str, Any]:  # noqa: ANN401 - a repo row
    """Export one repository row; return the exit code, stderr and the series posted."""
    repo = {"repo": "repo-x", "org": "org-x", "project": "proj-y"} | row
    repos = tmp_path / "repos.json"
    repos.write_text(json.dumps({"repos": [repo]}), encoding="utf-8")
    fake = Fake()
    with mock.patch.object(delivery, "REPOS", repos):
        code, errors = run(fake)
    return code, errors, series(fake.posted())


def test_a_checkout_without_a_changes_folder_still_exports(tmp_path: Path) -> None:
    """A checkout that holds no changes folder has PRs all the same; none names a change."""
    (tmp_path / "bare").mkdir()
    code, errors, sent = export(tmp_path, checkout=str(tmp_path / "bare"))
    assert (code, errors) == (0, ""), (code, errors)
    assert [dict(labels)["change"] for labels, _ in sent["dev_loop_pr"]] == [""] * 4
    assert "dev_loop_change" not in sent


def test_unreadable_records_keep_the_pr_series(tmp_path: Path) -> None:
    """Records that cannot be read are said and fail the run; the PR series still go."""
    repo = checkout(tmp_path)
    (repo / ".dev-loop" / "changes" / "alpha" / "records.jsonl").write_bytes(b"\xff\xfe not utf-8")
    code, errors, sent = export(tmp_path, checkout=str(repo))
    assert code == 1 and errors.count("\n") == 1, errors
    assert "repo-x records failed (UnicodeDecodeError)" in errors, errors
    assert len(sent["dev_loop_pr"]) == 4 and sent["dev_loop_merged_week"] == [((), 1.0)]
    assert "dev_loop_change" not in sent and "dev_loop_changes" not in sent  # not run, not clear


def changes_sent(sent: Any) -> set[str]:  # noqa: ANN401 - series
    return {dict(labels)["change"] for labels, _ in sent["dev_loop_change"]}


def test_the_archive_is_not_a_change(tmp_path: Path) -> None:
    """The archive folder holds finished changes; it is never exported as one."""
    repo = checkout(tmp_path)
    (repo / ".dev-loop" / "changes" / "archive" / "old").mkdir(parents=True)
    code, errors, sent = export(tmp_path, checkout=str(repo))
    assert (code, errors) == (0, ""), (code, errors)
    assert changes_sent(sent) == {"alpha", "beta"}


def test_the_checkout_config_names_the_folders(tmp_path: Path) -> None:
    """The checkout's own dev-loop.yaml says where its changes and its archive are."""
    repo = tmp_path / "configured"
    for name in ("alpha", "beta", "done/old"):
        (repo / "work" / "log" / name).mkdir(parents=True)
    (repo / "dev-loop.yaml").write_text(
        "version: 1\npaths:\n  changes: work/log\n  archive: work/log/done\n", encoding="utf-8")
    _, _, sent = export(tmp_path, checkout=str(repo))
    assert changes_sent(sent) == {"alpha", "beta"}
    _, _, sent = export(tmp_path, checkout=str(repo), changes="work/log/done")  # the row's own
    assert changes_sent(sent) == {"old"}
