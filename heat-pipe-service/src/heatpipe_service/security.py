"""Small helpers for keeping signed source URL queries out of diagnostics."""

from __future__ import annotations

import re

_URL_QUERY = re.compile(r"(https?://[^\s?'\"<>]+)\?[^\s'\"<>]+", re.IGNORECASE)


def redact_url_query(message: str) -> str:
    """Preserve a URL's origin/path while hiding its entire signed query string."""
    return _URL_QUERY.sub(r"\1?[REDACTED]", message)
