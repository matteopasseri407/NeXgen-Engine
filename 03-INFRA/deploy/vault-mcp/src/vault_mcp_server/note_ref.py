"""Which note a tool call means, whatever name the caller gave the argument.

One job: turn `note_ref` and its aliases into the single reference to use.
Why it exists: `read_note` takes `note_ref` while its siblings take `note_path`,
so models send `path` or `relative_path`, are refused by the schema and retry.
It lives outside `server.py` so it can be tested without the server's runtime
dependencies; `server.py` re-raises its `ValueError` as a model-readable tool error.
"""
from __future__ import annotations

#: The canonical parameter first, then the names models guess instead.
ALIASES = ("note_ref", "path", "note_path", "relative_path")


def resolve_note_ref(**given: str | None) -> str:
    """The one reference among `note_ref` and its aliases; a clear error for none or two different."""
    values = {(given.get(name) or "").strip() for name in ALIASES} - {""}
    if not values:
        raise ValueError(
            "no note reference given: pass 'note_ref' (or one of its aliases: 'path', 'note_path', 'relative_path')"
        )
    if len(values) > 1:
        raise ValueError(f"conflicting values for 'note_ref': {sorted(values)}; pass the note under one name")
    return values.pop()
