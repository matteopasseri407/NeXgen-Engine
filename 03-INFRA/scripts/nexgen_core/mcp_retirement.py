"""Which declared MCP server names must leave every CLI, and the memory that keeps that automatic.

Every CLI renderer preserves a live entry the manifest no longer declares,
because a person may have added it by hand. Two things win over that
preservation, and they are decided here so a removal can never depend on
someone remembering a tombstone:

- an exact name in `retired_servers`, the explicit tombstone;
- a name this engine itself declared at any earlier real render, remembered in
  the machine-local MCP state. `lane` was declared for OpenCode on 2026-09-27
  and dropped on 2026-10-24 without a tombstone, so every sync kept the stale
  entry and OpenCode failed to start it on each launch.

A name the engine never declared is never retired: it belongs to whoever put
it there. The memory is written by a real render only, never by a check, a
plan or a dry run.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from nexgen_core.config import load_mcp_manifest
from nexgen_core.mcp_state import load_declared_servers, record_declared_servers


def _allowlisted_names(raw: dict[str, Any] | None) -> set[str]:
    """Bare server names the manifest's `orphans_allowlist` keeps on purpose.

    The retirement view of that key, where the checks keep their own: an entry
    is a bare name or `cli:name`, and either way the person is saying that
    name stays, so a previously rendered entry it names is never debris.
    Anything malformed is ignored: this decides deletions, never a failure.
    """
    entries = (raw or {}).get("orphans_allowlist")
    if not isinstance(entries, list):
        return set()
    names: set[str] = set()
    for entry in entries:
        text = str(entry).strip()
        if text:
            names.add(text.split(":", 1)[-1])
    return names


def retired_names(manifest_path: Path, home: Path | None = None) -> set[str]:
    """The names to drop from every rendered config.

    The ranked decision behind :meth:`nexgen_core.renderer.McpRenderer.retired_server_names`,
    which documents the order: the active manifest wins, then tombstones and
    ended trials, then the names the engine declared and no longer does, and
    everything else is preserved.
    """
    retired: set[str] = set()
    manifest_names: set[str] = set()
    trials: set[str] = set()
    allowlisted: set[str] = set()
    if manifest_path.is_file():
        data = load_mcp_manifest(manifest_path)
        retired = set(data.get("retired_servers", set()))
        manifest_names = set(data.get("servers", {}))
        allowlisted = _allowlisted_names(data.get("raw"))
    try:
        from nexgen_core import mcp_trials

        retired |= {n for n in mcp_trials.retired_names() if n not in manifest_names}
        trials = set(mcp_trials.active_entries())
    except Exception:  # nexgen: allow reason="trial state failure must not stop manifest retirements"
        pass
    forgotten = load_declared_servers(home) - manifest_names - trials - allowlisted
    return (retired | forgotten) - manifest_names


def remember_declared(manifest_path: Path, home: Path | None = None) -> None:
    """Records the manifest's server names as what this render declared.

    Best effort on purpose: the configs are the product, and failing a sync
    because a note could not be written would be worse than losing the note.
    """
    try:
        names = set(load_mcp_manifest(manifest_path).get("servers", {})) if manifest_path.is_file() else set()
        record_declared_servers(names, home)
    except Exception:  # nexgen: allow reason="the memory is best effort and must never fail a render"
        pass
