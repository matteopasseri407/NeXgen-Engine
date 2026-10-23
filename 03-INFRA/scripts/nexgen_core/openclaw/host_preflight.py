"""What the host must provide before OpenClaw can be installed and kept running.

One job: ask the machine the questions setup and the doctor need answered, and
return facts, never messages. Node's version, npm's global prefix, the systemd
user bus, and lingering are four different sources; keeping them here means the
setup and the doctor cannot disagree about what this host can do.
"""
from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from pathlib import Path

from nexgen_core.openclaw_service import RunResult

#: Where systemd records the users allowed to keep services after logout.
LINGER_DIR = Path("/var/lib/systemd/linger")

Run = Callable[..., RunResult]
Which = Callable[[str], "str | None"]


def read_node_version(run: Run, node: str, env: Mapping[str, str]) -> str | None:
    """The installed Node version (`v24.22.1`), or None when it cannot be read."""
    try:
        result = run([node, "--version"], env)
    except (OSError, ValueError):  # nexgen: allow reason="a Node that cannot be asked is an unknown version, not a crash"
        return None
    text = (result.stdout or result.stderr or "").strip()
    return text.splitlines()[-1].strip() if text else None


def npm_prefix(run: Run, npm: str, env: Mapping[str, str]) -> Path | None:
    """The folder npm installs global packages into, or None when it will not say."""
    try:
        result = run([npm, "config", "get", "prefix"], env)
    except (OSError, ValueError):  # nexgen: allow reason="npm that cannot be asked means the prefix is unknown"
        return None
    lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    return Path(lines[-1]) if lines else None


def is_writable(directory: Path) -> bool:
    """Whether this user can create something in `directory` or its nearest parent."""
    probe = directory
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    return os.access(probe, os.W_OK)


def linger_enabled(user: str, directory: Path | None = None) -> bool:
    """Whether systemd keeps this user's services after the last session closes."""
    root = directory if directory is not None else LINGER_DIR
    return bool(user) and (root / user).exists()


def user_bus_missing(env: Mapping[str, str]) -> bool:
    """No session bus is configured in this process at all."""
    return not env.get("XDG_RUNTIME_DIR") and not env.get("DBUS_SESSION_BUS_ADDRESS")


def user_bus_unreachable(run: Run, env: Mapping[str, str]) -> bool:
    """`systemctl --user` cannot reach the bus it is configured for."""
    try:
        result = run(["systemctl", "--user", "show-environment"], env)
    except (OSError, ValueError):  # nexgen: allow reason="a systemctl that cannot run is reported as unreachable"
        return True
    text = f"{result.stdout or ''}\n{result.stderr or ''}"
    return "Failed to connect to bus" in text
