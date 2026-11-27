"""What the last groom run did, so the doctor can judge it instead of guessing.

One job: write one small record per real run -- when it ran, the cap it ran under,
how many chats it left waiting, how many it compacted, and which chats it could
not touch -- and read it back. Nothing here decides anything.

Why this exists: the hourly job runs with `--no-deliver`, its output reaches
nobody, and the scheduler keeps no output for a command job. The one question the
doctor cannot answer from the job row is what the run left behind, which is
exactly the owner's question after three days away: is a chat sitting at 900k
tokens with nothing scheduled to shrink it.

A missing or corrupt record means no run is known, never a crash, and a record
older than the job's last run is the doctor's sign that the job runs something
else.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from nexgen_core.files import write_private_text
from nexgen_core.openclaw import groom_memory
from nexgen_core.openclaw.sessions_rows import LaneChat
from nexgen_core.paths import resolve_state_dir

FILENAME = "openclaw-groom-run.json"
SCHEMA_VERSION = 1


def record_path(home: Path | None = None) -> Path:
    """Where this machine keeps the record of its last groom run."""
    return resolve_state_dir(home) / FILENAME


def load(home: Path | None = None) -> dict[str, Any] | None:
    """The last run's record, or None when no run was recorded or the file is unreadable."""
    try:
        data = json.loads(record_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):  # nexgen: allow reason="a missing or corrupt record means no run is known"
        return None
    return data if isinstance(data, dict) else None


def record(
    *,
    at_ms: float,
    cap: int,
    waiting: int,
    attempts: Iterable[tuple[Any, Any]],
    stranded: Iterable[LaneChat],
) -> dict[str, Any]:
    """One run described in the four numbers and the one list the doctor reads.

    `attempts` is what the run asked the gateway, as `(decision, outcome)` pairs:
    the two counters come from there, so a record cannot disagree with the lines
    the run printed. `stranded` is the chats on a lane with no idle compaction,
    listed by key, lane and size.
    """
    outcomes = [outcome.state for _, outcome in attempts]
    return {
        "schema_version": SCHEMA_VERSION,
        "at": at_ms,
        "cap": cap,
        "waiting": waiting,
        "compacted": sum(1 for state in outcomes if state == groom_memory.COMPACTED),
        "failed": sum(1 for state in outcomes if state == groom_memory.FAILED),
        "stranded": [{"key": chat.key, "lane": chat.lane, "tokens": chat.tokens} for chat in stranded],
    }


def save(recorded: Mapping[str, Any], home: Path | None = None) -> None:
    """Publish the record atomically: a crash never leaves half a run behind."""
    payload = dict(recorded)
    write_private_text(record_path(home), json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def failure(*, at_ms: float, error: str) -> dict[str, Any]:
    """A run that ended before deciding anything, in the one fact the doctor reads.

    It replaces the previous record, because it is the last thing that happened: the
    job still exited 0 after raising its alert, so the scheduler's own row says "ok"
    and this is the only place a person can learn the chats were never read.
    """
    return {"schema_version": SCHEMA_VERSION, "at": at_ms, "error": error}
