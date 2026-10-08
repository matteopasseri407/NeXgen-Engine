"""Single owner for third-party display names: 'skill X' -> 'X'."""

from __future__ import annotations


def short_name(what: str) -> str:
    import re

    match = re.match(r"^(?:skill|MCP server|module) '([^']+)'", str(what or ""))
    return match.group(1) if match else str(what or "")
