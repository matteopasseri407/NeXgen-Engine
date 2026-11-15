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
#: The gateway answered and compacted nothing (for example, nothing to do yet).
NOT_COMPACTED = "not compacted"
FAILED = "failed"

#: The outcomes after which an unchanged session is not attempted again.
SETTLED = frozenset({COMPACTED, ALREADY_COMPACTED, UNSUPPORTED, NOT_COMPACTED})


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
    payload = {"schema_version": 2, "sessions": dict(memory)}
    write_private_text(memory_path(home), json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def save_changed(
    memory: Mapping[str, dict[str, Any]],
    before: Mapping[str, dict[str, Any]],
    home: Path | None = None,
) -> bool:
    """Publish the memory only when this run changed it, and say whether it wrote.

    A run that changed nothing writes nothing: the file is compared, not rewritten,
    so an hourly job with nothing to do leaves the disk and its own mtime alone.
    """
    if dict(memory) == dict(before):
        return False
    save_memory(memory, home)
    return True


def settled(memory: Mapping[str, dict[str, Any]], key: str, mark: Mapping[str, float]) -> bool:
    """Whether this session, exactly as it is now, was already dealt with."""
    record = memory.get(key) or {}
    return record.get("outcome") in SETTLED and record.get("fingerprint") == dict(mark)


def quota_spent(memory: Mapping[str, dict[str, Any]], key: str, provider: str, now_ms: float) -> bool:
    """Whether this session, or any chat on its provider, hit a quota that has not renewed yet."""
    for known, record in memory.items():
        retry_at = record.get("retry_at")
        if isinstance(retry_at, (int, float)) and retry_at > now_ms and (
            known == key or (provider and record.get("provider") == provider)
        ):
            return True
    return False


def remember(
    memory: dict[str, dict[str, Any]],
    key: str,
    mark: Mapping[str, float],
    outcome: str,
    retry_at: float | None = None,
    provider: str = "",
    runtime: str = "",
) -> None:
    """Replace what is known about a session with the attempt just made.

    `runtime` is the backend that answered, so a chat the gateway refused is still
    nameable a day later: a memory keyed by outcome alone cannot say which chats
    are stuck behind a harness that will never compact them.
    """
    memory[key] = {"fingerprint": dict(mark), "outcome": outcome, "runtime": runtime}
    if retry_at is not None:
        memory[key].update(retry_at=retry_at, provider=provider)


def forget_runtime(memory: dict[str, dict[str, Any]], runtime: str) -> list[str]:
    """Forget every attempt made by one backend, and say which chats are offered again.

    A backend that could not answer before can answer now (a plugin update removed
    the manual compaction, and the groom put it back): what was remembered about it
    is a decision about a machine that no longer exists, and the unchanged
    fingerprint would otherwise keep those chats out of every future run.
    """
    dropped = [key for key, record in memory.items() if record.get("runtime") == runtime]
    for key in dropped:
        del memory[key]
    return dropped



def prune(memory: dict[str, dict[str, Any]], listed_keys: Iterable[Any]) -> None:
    """Forget the sessions the gateway no longer lists."""
    listed = {str(key) for key in listed_keys}
    for key in [known for known in memory if known not in listed]:
        del memory[key]
