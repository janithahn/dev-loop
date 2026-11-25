"""One check in the writer: a field value cannot carry a long quotation (document text, names).

Counted delimiters: straight double quotes, typographic double quotes and backticks (a run of
backticks closes with a run of the same length). A backtick span counts only when it holds
whitespace: a path or test id is one word, a command or a sentence is not. Unbalanced quotes may
mis-pair. A lesson's `class` field is skipped when it is a pitfalls row reference.
"""

from __future__ import annotations

import re
from typing import Any

LIMIT = 40
SPAN = re.compile(r'"([^"]*)"|“([^”]*)”|(`+)(.+?)\3', re.DOTALL)
HINT = ("say it in your own words (ids, rules and code names only) "
        "(a long command: name the test or script instead)")


def _long(match: re.Match[str]) -> bool:
    ticked = match.group(3) is not None
    text = match.group(4) if ticked else match.group(1) or match.group(2)
    return len(text) > LIMIT and not (ticked and not re.search(r"\s", text))


def quoted(record: dict[str, Any]) -> None:
    """Refuse a string field holding a quoted span of more than LIMIT characters; name the field."""
    for name, value in record.items():
        if name == "line" or not isinstance(value, str):
            continue
        if name == "class" and re.match(r"\d+ ", value):
            continue
        if any(_long(m) for m in SPAN.finditer(value)):
            raise ValueError(f"{name} quotes more than {LIMIT} characters: {HINT}")
