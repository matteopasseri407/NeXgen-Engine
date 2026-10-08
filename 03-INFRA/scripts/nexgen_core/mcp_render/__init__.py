"""One module per CLI dialect for MCP configuration rendering.

`McpRenderer` (`nexgen_core.renderer`) stays the facade: manifest loading,
server resolution, timeouts plumbing and the `render_all` fan-out. Each
module here owns exactly one CLI's native schema -- the part that breaks
every time a vendor ships a v2. Import direction is one-way: these modules
never import the facade back; shared constants live in this `__init__`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

#: Windows detection shared by every dialect module (shim names, junctions).
IS_WINDOWS = sys.platform == "win32"

#: Pinned bridge for CLIs without native remote support.
MCP_REMOTE_PACKAGE = "mcp-remote@0.1.38"


def load_json_object(path: Path) -> dict[str, Any]:
    """The JSON object a CLI keeps at `path`; an empty dict when there is no file.

    A file that exists but is not a JSON object stops the render with a
    message naming it: writing "our" version over it would discard
    whatever the user had there.
    """
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return {}  # an empty file holds nothing worth protecting
        data = json.loads(text)
    except Exception as exc:  # noqa: BLE001 - render fallback, never raises
        raise ValueError(f"Could not parse {path}: invalid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Could not parse {path}: expected a JSON object")
    return data


def write_json_config(renderer, path: Path, before: dict[str, Any], after: dict[str, Any]) -> bool:
    """Write `after` to `path` only when it says something different from `before`.

    The comparison is on meaning, not bytes. The CLI that owns the file
    formats it its own way (Claude Code writes JavaScript's `JSON.stringify`:
    non-ASCII kept as written, no trailing newline), and a bytes comparison
    against Python's formatting always differed, so every guard cycle
    rewrote the file, made a backup, and raced the CLI's own saves for a
    change that was only whitespace. Returns True when it wrote.
    """
    if path.is_file() and before == after:
        return False
    renderer._backup_and_write(path, json.dumps(after, indent=2, ensure_ascii=False) + "\n")
    return True
