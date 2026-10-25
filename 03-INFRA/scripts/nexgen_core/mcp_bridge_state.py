"""mcp_bridge_state.py — where a bridge run is recorded, and how a later command asks whether it is still there.

One job: the state file and the two authenticated calls that read it (`/health`,
`/shutdown`). It is separate from `mcp_bridge` for one reason that matters more than
line count: the file is the only thing that links two processes that never talk to
each other directly, and every rule about believing it belongs in one place.

The rule is that a state file is a claim, never a fact. It carries a nonce that only
the process that wrote it can echo back, so a file left behind by a killed run is
recognised as stale instead of making `status` report a listener that is not there.
Nothing secret is written here: the token is named, never copied.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from nexgen_core.files import atomic_write_text
from nexgen_core.mcp_bridge_server import HEALTH_PATH, SHUTDOWN_PATH
from nexgen_core.paths import resolve_state_dir

#: How long a health probe or a shutdown request may take. Loopback answers at once
#: or not at all; waiting longer only hides a dead port behind a spinner.
PROBE_TIMEOUT_SECONDS = 2.0

#: The loopback address, used only when a record does not say where it listens.
_FALLBACK_HOST = "127.0.0.1"


def state_file_path(home: Path | None = None) -> Path:
    """Where this machine records the bridge it started, so `stop` can find it again."""
    return resolve_state_dir(home) / "mcp_bridge.json"


def write_state(path: Path, state: dict[str, Any]) -> None:
    """Records one run, or the reason a run could not start.

    Through the engine's writer: a half-written record is a run `stop` cannot find,
    and the reader would report a bridge that is not there.
    """
    atomic_write_text(path, json.dumps(state, indent=2))


def read_state(path: Path) -> dict[str, Any] | None:
    """The recorded run, or None when there is none (or it is not readable as one)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # nexgen: allow reason="an absent or unreadable record is a run this machine does not know about"
        return None
    return data if isinstance(data, dict) else None


def endpoint_url(state: dict[str, Any], path: str = HEALTH_PATH) -> str:
    """The URL a probe calls for the listener this record describes."""
    host = str(state.get("client_host") or _FALLBACK_HOST)
    port = int(state.get("port") or 0)
    return f"http://{host}:{port}{path}"


def request_json(
    url: str,
    token: str | None,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    timeout: float = PROBE_TIMEOUT_SECONDS,
) -> tuple[int, dict[str, Any]]:
    """One call to the bridge; `(0, {})` when nothing answers. A `None` token sends none."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8") or "{}"
            return response.status, json.loads(payload)
    except urllib.error.HTTPError as exc:
        return exc.code, {}
    except (urllib.error.URLError, OSError, ValueError):
        return 0, {}


def verified(state: dict[str, Any], token: str) -> dict[str, Any] | None:
    """The health payload of the listener this record names, or None when it is not that listener."""
    status, payload = request_json(endpoint_url(state), token)
    if status == 200 and payload.get("nonce") == state.get("nonce"):
        return payload
    return None


def answering(state: dict[str, Any]) -> bool:
    """Whether anything at all holds the recorded port, when the token is not available."""
    status, _ = request_json(endpoint_url(state), token=None)
    return status != 0


def ask_to_stop(state: dict[str, Any], token: str) -> None:
    """Sends the shutdown request; the answer is the listener's business, the probe is ours."""
    request_json(endpoint_url(state, SHUTDOWN_PATH), token, method="POST", body={})


def public_record(state: dict[str, Any]) -> dict[str, Any]:
    """The parts of a state record a person may see: everything except the nonce."""
    return {
        key: state.get(key)
        for key in ("pid", "url", "bind_host", "client_host", "port", "servers", "started_at", "token_env")
        if key in state
    }
