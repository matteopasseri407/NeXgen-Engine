"""What the last groom attempt found, so a session that cannot get smaller is not retried forever.

One job: remember, per session key, the counters the gateway reported when the
last attempt ran and how that attempt ended. A session whose counters are
unchanged since an attempt that ended well is not offered again; an attempt that
really failed is. The file belongs to the engine, lives in the machine's state
directory, and a missing or corrupt copy means nothing was attempted yet, never
a crash.

Why this exists (issue #148): compaction was retried every hour against the same
session with the same counters, and every retry reported success.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from nexgen_core.files import write_private_text
from nexgen_core.paths import resolve_state_dir

FILENAME = "openclaw-groom.json"

#: How one attempt ended. `unsupported` means the gateway says its backend
#: cannot confirm a native compaction: a second attempt answers the same thing.
COMPACTED = "compacted"
ALREADY_COMPACTED = "already compacted"
UNSUPPORTED = "unsupported"
FAILED = "failed"

#: The outcomes after which an unchanged session is not attempted again.
SETTLED = frozenset({COMPACTED, ALREADY_COMPACTED, UNSUPPORTED})


def memory_path(home: Path | None = None) -> Path:
    """Where this machine remembers its groom attempts."""
    return resolve_state_dir(home) / FILENAME


def fingerprint(entry: Mapping[str, Any]) -> dict[str, float]:
    """The counters that have to move before another attempt is worth making."""
    stamp = entry.get("lastInteractionAt") or entry.get("updatedAt") or 0
    return {"tokens": int(entry.get("totalTokens") or 0), "stamp": float(stamp)}


def load_memory(home: Path | None = None) -> dict[str, dict[str, Any]]:
    """Every remembered attempt. A missing or corrupt file is an empty memory."""
    try:
        data = json.loads(memory_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):  # nexgen: allow reason="missing or corrupt memory means no attempt is known"
        return {}
    sessions = data.get("sessions") if isinstance(data, dict) else None
    if not isinstance(sessions, dict):
        return {}
    return {str(key): record for key, record in sessions.items() if isinstance(record, dict)}


def save_memory(memory: Mapping[str, dict[str, Any]], home: Path | None = None) -> None:
    """Publish the memory atomically: a crash never leaves half a fingerprint behind."""
    payload = {"schema_version": 1, "sessions": dict(memory)}
    write_private_text(memory_path(home), json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def settled(memory: Mapping[str, dict[str, Any]], key: str, mark: Mapping[str, float]) -> bool:
    """Whether this session, exactly as it is now, was already dealt with."""
    record = memory.get(key) or {}
    return record.get("outcome") in SETTLED and record.get("fingerprint") == dict(mark)


def remember(memory: dict[str, dict[str, Any]], key: str, mark: Mapping[str, float], outcome: str) -> None:
    """Replace what is known about a session with the attempt just made."""
    memory[key] = {"fingerprint": dict(mark), "outcome": outcome}


def prune(memory: dict[str, dict[str, Any]], listed_keys: Iterable[Any]) -> None:
    """Forget the sessions the gateway no longer lists."""
    listed = {str(key) for key in listed_keys}
    for key in [known for known in memory if known not in listed]:
        del memory[key]
