"""Single owner for terminal marks: safe fallback when the locale cannot encode."""

from __future__ import annotations

import sys


def safe_mark(mark: str, stream=sys.stdout) -> str:
    try:
        mark.encode(getattr(stream, "encoding", None) or "utf-8")
        return mark
    except (UnicodeEncodeError, TypeError):
        return {"✓": "[OK]", "✗": "[X]", "!": "[!]"} .get(mark, "[?]")


_ASCII_FALLBACKS = {
    "─": "-",
    "│": "|",
    "•": "*",
    "●": "*",
    "↳": "->",
    "·": "-",
    "—": "-",
    "✓": "[OK]",
    "✗": "[X]",
}


def safe_text(text: str, stream=None) -> str:
    """Full-string counterpart of safe_mark for dashboard output.

    Returns `text` untouched when the stream encodes it (the common
    UTF-8 case, JSON included); otherwise degrades the engine's box
    glyphs to ASCII so a Windows console on a legacy code page prints
    readable output instead of raising UnicodeEncodeError.

    `stream` defaults to the *current* sys.stdout, not the one bound
    at import time, so late replacements (tests, embedding) are honored.
    """
    if stream is None:
        stream = sys.stdout
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        text.encode(encoding)
        return text
    except (UnicodeEncodeError, TypeError, LookupError):
        pass
    out = []
    for ch in text:
        if ch in _ASCII_FALLBACKS:
            out.append(_ASCII_FALLBACKS[ch])
            continue
        try:
            ch.encode(encoding)
            out.append(ch)
        except (UnicodeEncodeError, TypeError):
            out.append("?")
    return "".join(out)
