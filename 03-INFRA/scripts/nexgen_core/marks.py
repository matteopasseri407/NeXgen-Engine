"""Single owner for terminal marks: safe fallback when the locale cannot encode."""

from __future__ import annotations

import sys


def safe_mark(mark: str, stream=sys.stdout) -> str:
    try:
        mark.encode(getattr(stream, "encoding", None) or "utf-8")
        return mark
    except (UnicodeEncodeError, TypeError):
        return {"✓": "[OK]", "✗": "[X]", "!": "[!]"} .get(mark, "[?]")
