"""Which stored sessions OpenClaw has archived, so the groom never asks to compact one.

One job: ask the Gateway for its archived sessions and drop those rows from a
session listing.

Why this exists: from 2026.11.10 `openclaw sessions list --all-agents --json`
returns archived sessions too, with no field that says so, and `sessions.compact`
refuses every one of them. The CLI listing is still the one the groom reads,
because the Gateway's `sessions.list` withholds the token counter of a chat whose
count is stale, the large chat that most needs compacting. Membership is asked
where OpenClaw states it: `sessions.list` with `{"archived": true}`.

An answer that cannot be read raises `UnreadableSessions`. Keeping the whole
listing instead would ask for archived chats again, without anybody knowing.
"""
from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Iterable
from typing import Any

#: Rows asked for per page; the Gateway says `hasMore` when there are others.
PAGE = 1000
_RPC_TIMEOUT_MS = 60_000

#: `(argv, timeout) -> CompletedProcess`: the groom's own executor.
Run = Callable[[list[str], int], "subprocess.CompletedProcess[str]"]


class UnreadableSessions(ValueError):
    """OpenClaw did not say which sessions there are, so a run cannot decide anything."""


def read_listing(proc: subprocess.CompletedProcess[str], asked: str) -> dict[str, Any]:
    """The JSON object a listing command printed, with its `sessions` list."""
    try:
        payload = json.loads(proc.stdout or "") if proc.returncode == 0 else None
    except ValueError:  # nexgen: allow reason="reported just below as an unreadable listing"
        payload = None
    if not isinstance(payload, dict) or not isinstance(payload.get("sessions"), list):
        said = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise UnreadableSessions(f"{asked}: {said[-1] if said else f'exit {proc.returncode}, no output'}")
    return payload


def archived_keys(binary: str, run: Run, timeout: int = 120) -> frozenset[str]:
    """Every session key the Gateway lists as archived, across agents and pages."""
    found: set[str] = set()
    offset = 0
    while True:
        params = json.dumps({"archived": True, "limit": PAGE, "offset": offset})
        proc = run([binary, "gateway", "call", "sessions.list", "--params", params, "--json",
                    "--timeout", str(_RPC_TIMEOUT_MS)], timeout)
        payload = read_listing(proc, "gateway call sessions.list")
        found.update(str(row["key"]) for row in payload["sessions"] if row.get("key"))
        following = payload.get("nextOffset")
        if not payload.get("hasMore") or not isinstance(following, int) or following <= offset:
            return frozenset(found)
        offset = following


def drop_archived(entries: Iterable[dict[str, Any]], binary: str, run: Run) -> list[dict[str, Any]]:
    """The listing without the sessions OpenClaw has archived."""
    rows = list(entries)
    if not rows:
        return rows
    archived = archived_keys(binary, run)
    return [entry for entry in rows if str(entry.get("key") or "") not in archived]
