"""read_only reads every common form of an agent definition's `tools:` (dev-loop 0.4.18).

An agent with no Bash cannot run the hand-off's record command and hands off in its final reply.
Bash alone is enough (a reviewer has no Write or Edit and still records). The names may be one
comma line, a flow list or a block list, quoted or not; `Bash(git:*)` is Bash; an unscoped
`disallowedTools` name is taken away. A line that cannot be read means "can record", the same as
no line at all: the gate then offers the record command, never a wrong reason to skip it.
Run: python -m pytest test_read_only.py -q (from the plugin's tests folder).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_neutral import check

from dev_loop.dashboard.gate import read_only

PLUGIN = Path(__file__).resolve().parents[1]
# the frontmatter lines under `name:`, and whether the agent is read-only
FORMS = [
    ("tools: Read, Grep, Glob\n", True),
    ("tools: Read, Grep, Bash\n", False),  # no Write or Edit: Bash still runs the record command
    ("tools: Read, Bash, Write\n", False),
    ("tools:\n  - Read\n  - Bash\n", False),  # a block list
    ("tools:\n- Read\n- Bash\n", False),  # ... not indented
    ("tools:\n  - Read\n  - Grep\nmodel: x\n", True),
    ("tools: [Read, Bash, Write]\n", False),  # a flow list
    ("tools: [Read, Grep]\n", True),
    ("tools:\n  [Read,\n   Bash]\n", False),  # ... over lines
    ('tools: ["Read", "Bash"]\n', False),  # quoted names
    ("tools:\n  - 'Read'\n  - \"Bash\"\n", False),
    ('tools: "Read, Grep"\n', True),
    ("tools: 'Read, Bash'\n", False),
    ("tools: Read, Bash(git:*)\n", False),  # a scoped tool is the tool
    ("tools: Read, Bash(git log:*, npm test:*), Grep\n", False),  # a scope's own commas
    ("tools: Read, Glob(src/**), Grep\n", True),
    ("tools: [Read, 'Bash(git:*)']\n", False),
    ("tools:\n  - Bash(git:*)\n", False),
    ("tools: Read, Bash\ndisallowedTools: Bash\n", True),  # taken away again
    ("tools: [Read, Bash]\ndisallowedTools:\n  - Bash\n", True),
    ("disallowedTools: [Bash, Write]\n", True),  # every tool but these
    ("disallowedTools: Write, Edit\n", False),
    ("tools: Read, Bash\ndisallowedTools: Bash(rm:*)\n", False),  # one command, not Bash
    ("description: no tools line, so every tool\n", False),
    ("tools: [Read, Grep\n", False),  # cannot be read: it can
    ("tools: *\n", False),
    ("tools: {Read: yes}\n", False),
    ("tools:\n", False),
    ("tools: Read\ndisallowedTools: [Bash\n", False),
]


@pytest.mark.parametrize(("front", "expected"), FORMS)
def test_every_form_of_the_tools_line_is_read(tmp_path: Path, front: str, expected: bool) -> None:
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    text = f"---\nname: scout\n{front}---\nThe agent's prompt.\n"
    (agents / "scout.md").write_text(text, "utf-8")
    assert read_only("scout", PLUGIN, tmp_path) is expected, front


def test_only_the_frontmatter_is_read(tmp_path: Path) -> None:
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "scout.md").write_text("---\nname: scout\n---\ntools: Read\n", "utf-8")
    assert not read_only("scout", PLUGIN, tmp_path)  # a line of the prompt, not a grant
    (agents / "plain.md").write_text("tools: Read\n", "utf-8")  # no frontmatter at all
    assert not read_only("plain", PLUGIN, tmp_path)
    (agents / "crlf.md").write_bytes(b"---\r\nname: crlf\r\ntools: Read, Grep\r\n---\r\n")
    assert read_only("crlf", PLUGIN, tmp_path)


def test_the_plugins_own_agents() -> None:
    """The researcher has no Bash; the reviewer has Bash and no Write, and records."""
    assert read_only("dev-loop:researcher", PLUGIN, PLUGIN)
    for name in ("code-reviewer", "planner", "builder", "review-fixer"):
        assert not read_only(f"dev-loop:{name}", PLUGIN, PLUGIN), name


def test_the_file_is_neutral() -> None:
    assert check([Path(__file__)]) == []
