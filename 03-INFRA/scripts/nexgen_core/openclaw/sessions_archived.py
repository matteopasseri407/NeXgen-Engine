"""Which stored sessions OpenClaw has archived, so the groom never asks to compact one.

One job: read the archived session keys out of every agent's own SQLite store,
read-only, and drop those rows from a session listing.

Why this exists: from 2026.11.10 `openclaw sessions list --all-agents --json`
returns archived sessions too (it reads `session_nodes` without the
`archived_at IS NULL` filter) but its JSON row carries neither `archived` nor
`archivedAt`. The groom took them for idle chats, and `sessions.compact` refuses
every archived session with "changed before compaction. Retry.", so each run
failed on one and the scheduler switched the hourly job off after ten.

The CLI offers no flag to leave them out, so the fact is read where OpenClaw
keeps it: `<state>/agents/<agent>/agent/openclaw-agent.sqlite`, table
`session_nodes`, whose `session_key` is the same `agent:<id>:...` key the CLI
prints. A store that is missing, locked, or shaped differently by a later
release adds nothing: the listing is then kept whole, which is the old
behaviour, never a guess.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any

#: Where each agent's store lives under OpenClaw's state folder.
STORE_GLOB = "agents/*/agent/openclaw-agent.sqlite"
_QUERY = "SELECT session_key FROM session_nodes WHERE archived_at IS NOT NULL"


def archived_keys(state_dir: Path | None) -> frozenset[str]:
    """Every session key some agent's store marks as archived; empty when none can be read."""
    if state_dir is None:
        return frozenset()
    found: set[str] = set()
    for store in sorted(Path(state_dir).glob(STORE_GLOB)):
        found.update(_store_archived(store))
    return frozenset(found)


def _store_archived(store: Path) -> set[str]:
    """The archived keys of one store, read without ever writing to it."""
    try:
        connection = sqlite3.connect(f"{store.as_uri()}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error:  # nexgen: allow reason="a store that cannot be opened hides nothing: the listing stays whole"
        return set()
    try:
        return {str(row[0]) for row in connection.execute(_QUERY) if row[0]}
    except sqlite3.Error:  # nexgen: allow reason="a locked store or another schema hides nothing: the listing stays whole"
        return set()
    finally:
        connection.close()


def drop_archived(entries: Iterable[dict[str, Any]], state_dir: Path | None) -> list[dict[str, Any]]:
    """The listing without the sessions OpenClaw has archived."""
    rows = list(entries)
    archived = archived_keys(state_dir)
    if not archived:
        return rows
    return [entry for entry in rows if str(entry.get("key") or "") not in archived]
