"""E rule 4: a PostToolUse hook reports an edit to a test file that is not additive."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
HOOK = PLUGIN / "hooks" / "additive-tests.py"
BASE = '''def test_a():
    assert 1 == 1
    assert 2 == 2


def test_b():
    assert True
'''
JS = '''it("adds", () => {
  expect(1).toBe(1);
});
'''


def repo(tmp_path: Path, name: str, text: str, globs: str = "'tests/*'") -> Path:
    root = tmp_path / "repo"
    (root / "tests").mkdir(parents=True)
    (root / "dev-loop.yaml").write_text("version: 1\nfix_proves: {tests: [" + globs + "]}\n")
    (root / name).write_text(text, encoding="utf-8")
    who = ["-c", "user.name=t", "-c", "user.email=noreply@example.invalid"]
    for args in (["init", "-q"], ["add", "-A"], [*who, "commit", "-qm", "x"]):
        subprocess.run(["git", "-C", str(root), *args], check=True)  # noqa: S603, S607
    return root


def edit(
    root: Path, name: str, text: str, tool: str = "Edit", response: object = None,
    project: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    (root / name).write_text(text, encoding="utf-8")
    event = {"tool_name": tool, "tool_input": {"file_path": str(root / name)}}
    if response is not None:
        event["tool_response"] = response
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(project or root)}
    return subprocess.run(  # noqa: S603 - this interpreter and our own hook
        [sys.executable, str(HOOK)], input=json.dumps(event), env=env,
        capture_output=True, text=True, check=False, timeout=30,
    )


@pytest.mark.parametrize(("name", "text", "new"), [
    ("tests/test_x.py", BASE, BASE.split("\n\n\ndef test_b")[0] + "\n"),  # a test removed
    ("tests/test_x.py", BASE, BASE.replace("    assert 2 == 2\n", "")),  # an assert removed
    ("tests/test_x.py", BASE, BASE.replace("def test_b", "@pytest.mark.skip\ndef test_b")),
    ("tests/test_x.py", BASE, BASE.replace("def test_b", "@pytest.mark.xfail\ndef test_b")),
    ("tests/x.test.ts", JS, ""),  # the only test removed
    ("tests/x.test.ts", JS, JS.replace("  expect(1).toBe(1);\n", "")),
    ("tests/x.test.ts", JS, JS.replace("it(", "it.only(")),
])
def test_a_non_additive_edit_is_reported(tmp_path: Path, name: str, text: str, new: str) -> None:
    done = edit(repo(tmp_path, name, text), name, new)
    assert done.returncode == 2
    assert "tests are additive only" in done.stderr


def test_an_edit_that_adds_a_test_is_allowed(tmp_path: Path) -> None:
    name = "tests/test_x.py"
    done = edit(repo(tmp_path, name, BASE), name, BASE + "\n\ndef test_c():\n    assert 3\n")
    assert (done.returncode, done.stderr) == (0, "")


def test_a_file_outside_the_test_globs_is_allowed(tmp_path: Path) -> None:
    root = repo(tmp_path, "src.py", BASE)
    assert edit(root, "src.py", "").returncode == 0


def test_a_new_test_file_is_allowed(tmp_path: Path) -> None:
    root = repo(tmp_path, "tests/test_x.py", BASE)
    assert edit(root, "tests/test_new.py", "def test_n():\n    pass\n").returncode == 0


@pytest.mark.parametrize(("name", "text", "new"), [
    ("tests/test_x.py", BASE, BASE.replace("def test_b", "def test_b_is_true")),
    ("tests/test_x.py", BASE, BASE.replace("test_a", "test_one").replace("test_b", "test_two")),
    ("tests/x.test.ts", JS, JS.replace('it("adds"', 'it("adds one and one"')),
])
def test_a_renamed_test_is_allowed(tmp_path: Path, name: str, text: str, new: str) -> None:
    done = edit(repo(tmp_path, name, text), name, new)
    assert (done.returncode, done.stderr) == (0, "")


def test_a_rename_that_also_drops_a_test_is_reported(tmp_path: Path) -> None:
    name = "tests/test_x.py"
    new = BASE.replace("test_a", "test_one").replace("def test_b():", "def helper():")
    done = edit(repo(tmp_path, name, BASE), name, new)
    assert done.returncode == 2
    assert "the edit removes a test;" in done.stderr


@pytest.mark.parametrize(("name", "text", "new"), [
    ("tests/test_x.py", BASE, BASE + "||def test_it_skips_blank_lines():|    assert True|"),
    ("tests/test_x.py", BASE, BASE.replace("def test_b", "# never skip this one|def test_b")),
    ("tests/test_x.py", BASE, BASE + "||def test_c():|    skipped = xfailed = 0|    assert 1|"),
    ("tests/x.test.ts", JS,
     JS + 'it("skips nothing, the only one", () => { expect(2).toBe(2); });|'),
])
def test_the_word_skip_in_a_name_or_a_comment_is_allowed(
    tmp_path: Path, name: str, text: str, new: str
) -> None:
    done = edit(repo(tmp_path, name, text), name, new.replace("|", chr(10)))
    assert (done.returncode, done.stderr) == (0, "")


@pytest.mark.parametrize(("name", "text", "new"), [
    ("tests/test_x.py", BASE,
     BASE.replace("def test_b", "@pytest.mark.skipif(True, reason='x')|def test_b")),
    ("tests/test_x.py", BASE,
     BASE.replace("    assert True", "    pytest.skip('later')|    assert True")),
    ("tests/test_x.py", BASE, BASE.replace("def test_b", "@unittest.skip('x')|def test_b")),
    ("tests/test_x.py", BASE, BASE.replace("def test_b", "@unittest.expectedFailure|def test_b")),
    ("tests/x.test.ts", JS, JS.replace("it(", "it.skip(")),
    ("tests/x.test.ts", JS, JS.replace("it(", "it.todo(")),
    ("tests/x.test.ts", JS, "describe.skip('all', () => {|" + JS + "});|"),
    ("tests/x.test.ts", JS, JS + "xdescribe('more', () => {});|"),
    ("tests/x_test.go", "func TestA(t *testing.T) {|}|",
     "func TestA(t *testing.T) {|  t.Skip()|}|"),
])
def test_a_marker_that_switches_a_test_off_is_reported(
    tmp_path: Path, name: str, text: str, new: str
) -> None:
    text = text.replace("|", chr(10))
    done = edit(repo(tmp_path, name, text), name, new.replace("|", chr(10)))
    assert done.returncode == 2
    assert "adds a skip, xfail or only" in done.stderr


UNIT = """class T(unittest.TestCase):
    def test_a(self):
        self.assertEqual(1, 1)
        with pytest.raises(ValueError):
            int("x")
        mock.assert_called_once()
"""
SHOULD = 'it("adds", () => {\n  sum.should.equal(2);\n});\n'
OFF = BASE.replace("def test_b():\n    assert True", "# def test_b():\n#     assert True")
BOTH = "removes a test and removes an assert"
DEAD = "adds an early return or an if False"


@pytest.mark.parametrize(("name", "text", "new", "said"), [
    ("tests/test_x.py", BASE, OFF, BOTH),  # a test commented out
    ("tests/x.test.ts", JS, "".join(f"// {line}\n" for line in JS.splitlines()), BOTH),
    ("tests/x.test.ts", JS, "/*\n" + JS + "*/\n", BOTH),
    ("tests/test_x.py", UNIT, UNIT.replace("        self.assertEqual(1, 1)\n", ""),
     "removes an assert"),
    ("tests/test_x.py", UNIT, UNIT.replace("with pytest.raises(ValueError):", "if True:"),
     "removes an assert"),
    ("tests/test_x.py", UNIT, UNIT.replace("        mock.assert_called_once()\n", ""),
     "removes an assert"),
    ("tests/x.test.ts", SHOULD, SHOULD.replace("  sum.should.equal(2);\n", ""),
     "removes an assert"),
    ("tests/test_x.py", BASE, BASE.replace("    assert True", "    if False:\n        assert True"),
     DEAD),
    ("tests/test_x.py", BASE, BASE.replace("    assert True", "    return\n    assert True"), DEAD),
])
def test_a_test_switched_off_another_way_is_reported(
    tmp_path: Path, name: str, text: str, new: str, said: str
) -> None:
    done = edit(repo(tmp_path, name, text), name, new)
    assert done.returncode == 2
    assert f"additive-tests: the edit {said}; tests are additive only" in done.stderr


NOTED = BASE.replace("def test_b", "# assert on the flag later|def test_b").replace("|", chr(10))


@pytest.mark.parametrize(("name", "text", "new"), [
    ("tests/test_x.py", NOTED, BASE),  # a comment that said assert is taken away
    ("tests/test_x.py", BASE, BASE + "||def helper(value):|    return value|"),
    ("tests/test_x.py", BASE, BASE.replace("def test_b", "# if False: this stays on|def test_b")),
])
def test_a_comment_or_a_helper_is_not_counted(
    tmp_path: Path, name: str, text: str, new: str
) -> None:
    done = edit(repo(tmp_path, name, text), name, new.replace("|", chr(10)))
    assert (done.returncode, done.stderr) == (0, "")


def test_a_glob_with_a_leading_double_star_matches_the_top_level_like_fix_proves(
    tmp_path: Path,
) -> None:
    name = "tests/test_x.py"
    root = repo(tmp_path, name, BASE, globs="'**/tests/*'")
    done = edit(root, name, BASE.replace("def test_b():\n    assert True\n", ""))
    assert done.returncode == 2 and "removes a test" in done.stderr


def test_the_glob_is_matched_from_the_project_root_not_the_git_top(tmp_path: Path) -> None:
    top = repo(tmp_path, "tests/keep.py", "x = 1\n")
    project = top / "proj"
    (project / "tests").mkdir(parents=True)
    (project / "dev-loop.yaml").write_text("version: 1\nfix_proves: {tests: ['tests/*']}\n")
    name = "proj/tests/test_x.py"
    (top / name).write_text(BASE, encoding="utf-8")
    commit = ["-c", "user.name=t", "-c", "user.email=noreply@example.invalid", "commit", "-qm", "y"]
    for args in (["add", "-A"], commit):
        subprocess.run(["git", "-C", str(top), *args], check=True)  # noqa: S603, S607
    done = edit(top, name, BASE.replace("def test_b():\n    assert True\n", ""), project=project)
    assert done.returncode == 2 and "removes a test" in done.stderr
    outside = edit(top, "tests/keep.py", "", project=project)  # not under the project: no glob
    assert (outside.returncode, outside.stderr) == (0, "")


GONE = BASE.replace("def test_b():\n    assert True\n", "")


def test_the_text_before_the_edit_is_the_one_the_event_carries(tmp_path: Path) -> None:
    name = "tests/test_x.py"
    root = repo(tmp_path, name, GONE)  # HEAD already lacks test_b
    done = edit(root, name, GONE, response={"originalFile": BASE})
    assert done.returncode == 2 and "removes a test" in done.stderr
    same = edit(root, name, GONE, response={"originalFile": GONE})  # this edit removed nothing
    assert (same.returncode, same.stderr) == (0, "")


@pytest.mark.parametrize("response", [{}, {"originalFile": None}, {"originalFile": 3}, "ok"])
def test_without_an_original_file_the_text_at_head_is_the_before(
    tmp_path: Path, response: object
) -> None:
    name = "tests/test_x.py"
    root = repo(tmp_path, name, BASE)
    done = edit(root, name, GONE, response=response)
    assert done.returncode == 2 and "removes a test" in done.stderr


ROWS = """@pytest.mark.parametrize(("a", "b"), [
    (1, "x]"),
    (2, [3, 4]),
    pytest.param(5, "y", id="five"),
])
def test_rows(a, b):
    assert a
"""
ROW_ONE = '    (1, "x]"),\n'


@pytest.mark.parametrize(("new", "code"), [
    (ROWS.replace(ROW_ONE, ""), 2),  # a row removed
    (ROWS.replace('    pytest.param(5, "y", id="five"),\n', ""), 2),
    (ROWS.replace("(2, [3, 4]),", "(2, [3, 4]), (6, [7]),"), 0),  # a row added
    (ROWS.replace('"x]"', '"z"'), 0),  # a row changed, same count
    (ROWS.replace(ROW_ONE, "").replace("    (2, [3, 4]),\n", "    (2, [3, 4]),\n" + ROW_ONE), 0),
])
def test_a_removed_parametrize_row_is_reported_like_a_removed_test(
    tmp_path: Path, new: str, code: int
) -> None:
    name = "tests/test_x.py"
    done = edit(repo(tmp_path, name, ROWS), name, new)
    assert done.returncode == code
    assert code == 0 or "the edit removes a test;" in done.stderr
