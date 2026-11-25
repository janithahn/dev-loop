"""`dl review fix-brief`: a fix builder's brief carries the review's own standard (v0.4.21).

The threads come on stdin as `dl host threads --json` prints them; the brief goes to stdout and
the reviewer's context to a file under the git dir.
"""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
from pathlib import Path

import pytest
import yaml
from dev_loop.review import fix_brief, run

PLUGIN = Path(__file__).resolve().parents[1]
SPEC = """# Spec

## Acceptance
- the widget in a.py rounds half up
- the report lists every row
"""
PROPOSAL = """# Proposal

### Out of scope
- the export format stays as it is

## Why
- not a decision
"""
RULE = "Every money value is rounded half up."
CRID = "\n\n`crid:0123456789ab`"


def hook_gaps() -> object:
    hook = PLUGIN / "hooks/brief-contract.py"
    spec = importlib.util.spec_from_file_location("brief_contract", hook)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.gaps


def defaults() -> dict[str, object]:
    text = (PLUGIN / "src/dev_loop/defaults.yaml").read_text(encoding="utf-8")
    return yaml.safe_load(text)["briefs"]


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "dev-loop.yaml").write_text("version: 1\n", encoding="utf-8")
    change = repo / ".dev-loop/changes/ch1"
    change.mkdir(parents=True)
    (change / "proposal.md").write_text(PROPOSAL, encoding="utf-8")
    (change / "spec.md").write_text(SPEC, encoding="utf-8")
    (repo / "src/a.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "src/CLAUDE.md").write_text(f"# Area\n\n{RULE}\n", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
    return repo


def thread(tid: int, path: str, text: str = "**major** a.py rounds  half\ndown.") -> dict:
    return {"id": tid, "status": "active", "comments": [{"content": text}],
            "threadContext": {"filePath": f"/{path}", "rightFileStart": {"line": 3}}}


def brief(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
          threads: list[dict], ids: list[str]) -> tuple[int, str, str]:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(threads)))
    code = fix_brief.main(["ch1", *ids])
    out, err = capsys.readouterr()
    return code, out, err


def section(out: str, name: str) -> str:
    return out.split(f"## {name}\n", 1)[1].split("\n## ", 1)[0]


def context_path(out: str) -> Path:
    line = next(x for x in section(out, "Read first").splitlines() if "dl-fix-brief-" in x)
    return Path(line[2:].split(":", 1)[0].split(" ", 1)[0])


def test_one_major_gives_a_brief_the_contract_passes(root: Path, monkeypatch, capsys) -> None:
    code, out, err = brief(monkeypatch, capsys, [thread(7, "src/a.py", f"**major** a.py rounds  "
                                                     f"half\ndown.{CRID}")], ["7"])
    assert code == 0, err
    wanted = defaults()
    assert hook_gaps()(out, wanted["sections"], wanted["max_read_first"]) == []
    goal = section(out, "Goal")
    assert "Fix review thread 7 (src/a.py:3, major): **major** a.py rounds half down." in goal
    assert "crid:" not in goal
    read = section(out, "Read first")
    assert "- src/a.py" in read
    assert context_path(out).read_text(encoding="utf-8") == run.context(root, ["src/a.py"])
    assert RULE in context_path(out).read_text(encoding="utf-8")
    done = section(out, "Done when")
    assert "- Thread 7: its new test fails before the fix and passes after." in done
    assert "the widget in a.py rounds half up" in done and "every row" not in done
    decisions = section(out, "Decisions")
    assert "- the export format stays as it is" in decisions and "not a decision" not in decisions
    assert "- A guardrail is never undone to silence a finding." in decisions
    assert section(out, "Rules").strip()


def test_a_person_s_comment_is_refused_by_name(root: Path, monkeypatch, capsys) -> None:
    code, out, err = brief(monkeypatch, capsys, [thread(8, "src/a.py", "please look")], ["8"])
    assert code == 2 and "dl review fix-brief:" in err and "8" in err and out == ""


def test_an_unknown_thread_is_refused(root: Path, monkeypatch, capsys) -> None:
    code, _, err = brief(monkeypatch, capsys, [thread(7, "src/a.py", f"**major** x{CRID}")], ["9"])
    assert code == 2 and "9" in err


def test_bad_json_and_no_thread_are_refused(root: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("{not json"))
    assert fix_brief.main(["ch1", "7"]) == 2
    assert brief(monkeypatch, capsys, [], [])[0] == 2


def test_files_over_the_cap_are_named_in_goal(root: Path, monkeypatch, capsys) -> None:
    (root / "dev-loop.yaml").write_text("version: 1\nbriefs: {max_read_first: 6}\n", "utf-8")
    threads = [thread(i, f"src/f{i}.py", f"**minor** f{i}{CRID}") for i in range(1, 8)]
    code, out, err = brief(monkeypatch, capsys, threads, [str(i) for i in range(1, 8)])
    assert code == 0, err
    bullets = [x for x in section(out, "Read first").splitlines() if x.startswith("- ")]
    assert len(bullets) == 6 and "dl-fix-brief-1.md" in bullets[-1]
    assert "+2 more files" in section(out, "Goal")


def test_the_change_line_comes_first(root: Path, monkeypatch, capsys) -> None:
    (root / "dev-loop.yaml").write_text("version: 1\nbriefs: {change_line: true}\n", "utf-8")
    code, out, _ = brief(monkeypatch, capsys, [thread(7, "src/a.py", f"**major** x{CRID}")], ["7"])
    assert code == 0 and out.splitlines()[0] == "Change: ch1"


def test_a_section_the_tool_has_no_text_for_says_none(root: Path, monkeypatch, capsys) -> None:
    config = "version: 1\nbriefs: {sections: [Goal, Read first, Done when, Report]}\n"
    (root / "dev-loop.yaml").write_text(config, "utf-8")
    code, out, _ = brief(monkeypatch, capsys, [thread(7, "src/a.py", f"**major** x{CRID}")], ["7"])
    assert code == 0 and section(out, "Report").strip() == "none"


def test_the_review_skill_dispatches_the_brief_and_has_stop_points() -> None:
    text = (PLUGIN / "skills/review/SKILL.md").read_text(encoding="utf-8")
    assert "dl review fix-brief" in text
    assert sum(line.startswith("Stop point:") for line in text.splitlines()) >= 2


def test_a_posted_finding_with_its_emoji_keeps_its_severity(
        root: Path, monkeypatch, capsys) -> None:
    posted = f"\U0001f7e0 **major** \u2014 a.py rounds half down.{CRID}"
    code, out, err = brief(monkeypatch, capsys, [thread(7, "src/a.py", posted)], ["7"])
    assert code == 0, err
    assert "(src/a.py:3, major)" in section(out, "Goal")


@pytest.mark.parametrize("footer", ["`crid:summary`", "`crid:skip`"])
def test_a_summary_or_skip_thread_is_not_a_finding(
        root: Path, monkeypatch, capsys, footer: str) -> None:
    text = f"**major** x\n\n{footer}"
    code, out, _ = brief(monkeypatch, capsys, [thread(7, "src/a.py", text)], ["7"])
    assert code == 2 and out == ""


def test_a_silent_stdin_is_refused_unread(root: Path, monkeypatch, capsys) -> None:
    class Never(io.StringIO):
        def read(self, *_: object) -> str:
            raise AssertionError("read a silent stdin")

    monkeypatch.setattr("sys.stdin", Never())
    monkeypatch.setattr(fix_brief, "silent_stdin", lambda: True)
    assert fix_brief.main(["ch1", "7"]) == 2
    assert "stdin" in capsys.readouterr().err


def test_stdin_waits_longer_than_the_host_listing() -> None:
    assert fix_brief.silent_stdin.args == (120,)  # the host listing takes up to 60 s


def test_done_when_matches_a_whole_path_never_a_substring(root: Path, monkeypatch,
                                                          capsys) -> None:
    (root / ".dev-loop/changes/ch1/spec.md").write_text(
        "# Spec\n\n## Acceptance\n- data.py keeps every row\n"
        "- skills/build/SKILL.md names the wave\n- review/SKILL.md names the stop\n"
        "- the widget in a.py rounds half up\n", encoding="utf-8")
    threads = [thread(7, "src/a.py", f"**major** x{CRID}"),
               thread(8, "skills/review/SKILL.md", f"**major** y{CRID}")]
    code, out, err = brief(monkeypatch, capsys, threads, ["7", "8"])
    assert code == 0, err
    done = section(out, "Done when")
    assert "the widget in a.py rounds half up" in done and "names the stop" in done
    assert "data.py" not in done and "skills/build" not in done
