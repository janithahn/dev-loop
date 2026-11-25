"""The exporters' one transport.

`fetch`: https only, never redirected (a 3xx raises). Nothing here prints or logs a value.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_: object) -> None:  # a 3xx raises: it would carry the auth on
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def fetch(request: urllib.request.Request) -> Any:  # noqa: ANN401 - JSON
    if not request.full_url.startswith("https://"):  # the auth header never goes in clear
        raise ValueError("https only")  # noqa: EM101, TRY003
    with OPENER.open(request, timeout=10) as response:
        text = response.read()
    return json.loads(text) if text else {}

