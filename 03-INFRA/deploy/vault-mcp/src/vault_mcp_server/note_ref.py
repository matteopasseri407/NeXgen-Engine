"""Which note a `read_note` call means, whatever name the caller gave the argument.

One job: turn `note_ref` and its aliases into the single reference to read.
Why it exists: sibling tools name their parameter `note_path`, so models call
`read_note` with `path` or `relative_path`, were refused by the schema and
retried. It lives outside `server.py` so it can be tested without the server's
runtime dependencies.
"""
from __future__ import annotations

#: The canonical parameter first, then the names models guess instead.
ALIASES = ("note_ref", "path", "note_path", "relative_path")


def resolve_note_ref(**given: str | None) -> str:
    """The one reference among `note_ref` and its aliases; a clear error for none or two different."""
    values = {(given.get(name) or "").strip() for name in ALIASES} - {""}
    if not values:
        raise ValueError("read_note requires 'note_ref' (or an alias: 'path', 'note_path', 'relative_path')")
    if len(values) > 1:
        raise ValueError(f"read_note received conflicting values for 'note_ref': {sorted(values)}")
    return values.pop()
