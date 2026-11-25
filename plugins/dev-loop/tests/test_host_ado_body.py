"""The ADO adapter cuts a PR description to the host's 4000 and names the host's refusal."""

from __future__ import annotations

import base64
import io
import json
import urllib.error
import urllib.request
from typing import Any

import pytest

from dev_loop.hosts import HostError, ado

CUT = "(cut: the whole text is in the change's proposal.md)"


def sent_description(monkeypatch: pytest.MonkeyPatch, body: str) -> str:
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(ado, "_repo", lambda: "/repo")
    monkeypatch.setattr(ado, "request", lambda m, p, b: seen.append(b) or {"pullRequestId": 1})
    assert ado.pr_create("lp/x", "main", "T", body) == 1
    return str(seen[0]["description"])


def test_a_long_description_is_cut_to_4000_with_the_cut_line(monkeypatch) -> None:
    sent = sent_description(monkeypatch, "x" * 5000)
    assert len(sent) <= 4000
    assert sent.endswith(CUT)


def test_a_short_description_is_sent_unchanged(monkeypatch) -> None:
    assert sent_description(monkeypatch, "y" * 100) == "y" * 100


def test_an_emoji_description_is_cut_by_utf16_units(monkeypatch) -> None:
    sent = sent_description(monkeypatch, "\U0001f600" * 3990)
    assert len(sent.encode("utf-16-le")) // 2 <= 4000
    assert sent.endswith(CUT)


def refused(monkeypatch: pytest.MonkeyPatch, page: bytes, auth: str = "") -> str:
    def urlopen(req: Any, timeout: float) -> Any:  # noqa: ANN401, ARG001
        raise urllib.error.HTTPError(
            "http://h", 400, "Bad", {}, io.BytesIO(page))  # type: ignore[arg-type]

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    req = urllib.request.Request("http://h", data=b'{"secret": 1}')  # noqa: S310
    if auth:
        req.add_unredirected_header("Authorization", auth)
    with pytest.raises(HostError) as error:
        ado._send(req)
    assert error.value.status == 400
    return str(error.value)


def test_the_token_and_its_header_value_are_masked(monkeypatch) -> None:
    coded = base64.b64encode(b":tok").decode()
    page = json.dumps({"message": f"bad tok and {coded}"}).encode()
    said = refused(monkeypatch, page, f"Basic {coded}")
    assert said == "host answered HTTP 400: bad *** and ***"


def test_a_bearer_value_is_masked(monkeypatch) -> None:
    page = b'{"message": "bad Bearer abc123"}'
    said = refused(monkeypatch, page, "Bearer abc123")
    assert "abc123" not in said
    assert said.startswith("host answered HTTP 400: bad ")


def test_a_json_refusal_names_the_host_message(monkeypatch) -> None:
    assert refused(monkeypatch, b'{"message": "too long"}') == "host answered HTTP 400: too long"


def test_a_long_multiline_message_is_one_line_of_200(monkeypatch) -> None:
    text = refused(monkeypatch, b'{"message": "a\nb' + b"c" * 300 + b'"}')
    assert "\n" not in text
    assert len(text) <= len("host answered HTTP 400: ") + 200


def test_an_html_refusal_is_as_before(monkeypatch) -> None:
    assert refused(monkeypatch, b"<html>no</html>") == "host answered HTTP 400"
