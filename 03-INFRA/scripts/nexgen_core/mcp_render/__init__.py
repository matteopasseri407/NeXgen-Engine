"""One module per CLI dialect for MCP configuration rendering.

`McpRenderer` (`nexgen_core.renderer`) stays the facade: manifest loading,
server resolution, timeouts plumbing and the `render_all` fan-out. Each
module here owns exactly one CLI's native schema -- the part that breaks
every time a vendor ships a v2. Import direction is one-way: these modules
never import the facade back; shared constants and the one entry shape two
dialects need live in this `__init__`.
"""
from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path
from typing import Any

#: Windows detection shared by every dialect module (shim names, junctions).
IS_WINDOWS = sys.platform == "win32"

#: Pinned bridge for CLIs without native remote support.
MCP_REMOTE_PACKAGE = "mcp-remote@0.1.38"

#: An interpreter a dialect must give a UTF-8 environment to: `python`,
#: `python3`, `python3.12`, `py`, and the same names as a Windows `.exe`,
#: including the copy a virtualenv keeps under `Scripts/`.
_PYTHON_COMMAND = re.compile(r"^(python[0-9.]*|py)(\.exe)?$", re.I)

#: A numeric number of seconds, as YAML hands it over when it was quoted.
_NUMERIC_SECONDS = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*$")


def is_python_command(command: str) -> bool:
    """Whether this command line starts a Python interpreter."""
    return bool(_PYTHON_COMMAND.match(Path(command).name))


def milliseconds(timeouts: Any, key: str) -> int | None:
    """A manifest timeout (seconds) as the positive milliseconds a CLI schema wants.

    A value that is not a number is no timeout, never a crash: the field is
    optional and a broken one must not stop the other servers from mounting.
    """
    raw = timeouts.get(key) if isinstance(timeouts, dict) else None
    match = _NUMERIC_SECONDS.match(raw) if isinstance(raw, str) else None
    if match:
        millis = int(float(match.group(1)) * 1000)
    elif isinstance(raw, (int, float)) and not isinstance(raw, bool) and math.isfinite(raw):
        millis = int(float(raw) * 1000)
    else:
        return None
    return millis if millis > 0 else None


def http_bridge_entry(renderer, srv: dict[str, Any]) -> dict[str, Any]:
    """The stdio entry that reaches an HTTP MCP server through the engine's pinned bridge.

    A CLI can only be handed a child process, so a remote server is mounted as
    `node mcp-http-bridge.mjs <url> <token variable name> <package>`: the bridge
    reads the bearer from that variable in the environment of the process that
    starts it, so no generated config ever holds the value. Antigravity and the
    Desktop app both write exactly this shape; OpenClaw writes its own variant,
    which adds its `${VAR}` references for it to substitute at spawn.
    """
    bridge_script = renderer.engine_root / "agent-universal-layer" / "mcp" / "mcp-http-bridge.mjs"
    auth_env = srv.get("auth", {}).get("env") if isinstance(srv.get("auth"), dict) else ""
    node_cmd = "node.exe" if IS_WINDOWS else "node"
    return {
        "command": node_cmd,
        "args": [str(bridge_script), srv["url"], auth_env, MCP_REMOTE_PACKAGE],
        "env": srv.get("env", {}),
    }


def load_json_object(path: Path) -> dict[str, Any]:
    """The JSON object a CLI keeps at `path`; an empty dict when there is no file.

    A file that exists but is not a JSON object stops the render with a
    message naming it: writing "our" version over it would discard
    whatever the user had there.
    """
    if not path.is_file():
        return {}
    try:
        # `utf-8-sig`: a configuration file saved by an editor that writes a BOM
        # (PowerShell 5.1's `Set-Content -Encoding utf8` does) is still a JSON object
        # OpenCode reads; refusing it stopped the render on a file that was fine.
        text = path.read_text(encoding="utf-8-sig")
        if not text.strip():
            return {}  # an empty file holds nothing worth protecting
        data = json.loads(text)
    except Exception as exc:  # noqa: BLE001 - render fallback, never raises
        raise ValueError(f"Could not parse {path}: invalid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Could not parse {path}: expected a JSON object")
    return data


def merge_entry(previous: Any, fresh: dict[str, Any], owned: frozenset[str]) -> dict[str, Any]:
    """`fresh`, plus every key the existing entry holds that this engine does not write.

    Each app keeps per-server state of its own beside the launch fields: the
    `disabled` toggle, the tool allow and deny lists, the eager flags. Writing the
    entry from scratch dropped exactly that state on the next guard cycle, so a
    server switched off in the app's own screen came back on within half an hour,
    without a word. `owned` names the keys the renderer writes; everything else in
    `previous` is left where it was, which also preserves the app's own key order.
    """
    if not isinstance(previous, dict):
        return fresh
    merged = {key: value for key, value in previous.items() if key not in owned}
    merged.update(fresh)
    return merged


def servers_written_for(renderer, cli: str) -> dict[str, Any]:
    """The servers `cli`'s own file must carry on this machine, as its dialect writes them.

    Asked by the doctor, so its verdict and the render cannot disagree. Antigravity's
    list depends on whether OpenClaw drives the same `agy`; every other CLI writes the
    resolved manifest as it is.
    """
    if cli == "antigravity":
        from nexgen_core.mcp_render.antigravity import _expected_servers

        return _expected_servers(renderer)
    return renderer.load_resolved_servers(cli)


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
