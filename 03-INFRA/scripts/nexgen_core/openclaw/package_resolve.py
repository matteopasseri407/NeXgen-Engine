# nexgen: one-job reason="locate the OpenClaw package directory from an executable path on PATH"
"""Resolve an OpenClaw binary on PATH to its package directory and manifest.

On Linux/macOS OpenClaw is often installed into a user prefix (e.g. ~/.local/node24)
and symlinked into ~/.local/bin/openclaw. Following symlinks leads into the package
directory.
On Windows, npm installs an openclaw.cmd batch shim beside node_modules/openclaw.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _read_manifest(path: Path) -> dict[str, Any] | None:
    """Read a JSON package manifest, or None when absent or malformed."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):  # nexgen: allow reason="unreadable package.json means no valid package at this path"
        return None


def resolve_openclaw_package(binary: str | Path | None) -> Path | None:
    """Resolve an OpenClaw executable to its package directory, following symlinks.

    Returns the directory containing OpenClaw's package.json, or None when the binary
    does not lead to a valid OpenClaw package.
    """
    if not binary:
        return None
    try:
        path = Path(binary).resolve()
    except (OSError, RuntimeError):  # nexgen: allow reason="a binary path that cannot be resolved cannot lead to an on-disk package"
        path = Path(binary)

    # 1. Binary is inside the package (e.g. symlink to node_modules/openclaw/...)
    for parent in (path.parent, *path.parents):
        manifest_file = parent / "package.json"
        if manifest_file.is_file():
            manifest = _read_manifest(manifest_file)
            if manifest and manifest.get("version"):
                if manifest.get("name") == "openclaw" or parent.name == "openclaw":
                    return parent

    # 2. Binary is an npm shim beside node_modules/openclaw (e.g. Windows .cmd shim)
    #    or in a prefix bin/ beside lib/node_modules/openclaw (POSIX prefix)
    for rel in (
        Path("node_modules") / "openclaw",
        Path("lib") / "node_modules" / "openclaw",
    ):
        candidate = path.parent / rel
        manifest_file = candidate / "package.json"
        if manifest_file.is_file():
            manifest = _read_manifest(manifest_file)
            if manifest and manifest.get("version"):
                return candidate

    return None
