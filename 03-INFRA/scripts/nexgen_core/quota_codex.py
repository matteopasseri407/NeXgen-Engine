"""What Codex itself says is left of its rate limit, read from its own session files.

One job: find the newest `rate_limits` record Codex wrote under
`$CODEX_HOME/sessions` and hand it back with the moment it was written. The
percentage is Codex's own number, never one this engine computed from a guess,
and a machine with no session that carries a reading says so instead of zero.

The shape was read from a live session on 2026-10-24: one JSON object per line,
and the token-count events carry
`payload.rate_limits.{primary,secondary,credits}` with `used_percent`,
`window_minutes` and `resets_at` (epoch seconds). Only the tail of a file is
read, newest line first: a long session is hundreds of megabytes of history and
the answer is always in the last few kilobytes that mention a limit.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from nexgen_core.i18n import t

#: How much of a session file is read per step while walking backwards from its end.
TAIL_CHUNK_BYTES = 262_144
#: How much of one session is read at most. A session that mentions no limit in
#: this much of its tail is not going to mention one further back: the reading
#: is written by the last turn, and a session with hours of history behind it
#: must not cost seconds to rule out.
TAIL_BUDGET_BYTES = 8 * 1024 * 1024
#: How many sessions may be consulted before giving up: a machine whose newest
#: handful of sessions never hit a limit has no current reading to report.
MAX_SESSIONS = 5


@dataclass(frozen=True)
class RateLimit:
    """One window of Codex's own limit: how much of it is used, and when it resets."""

    used_percent: float
    window_minutes: int | None = None
    resets_at: int | None = None


@dataclass(frozen=True)
class CodexUsage:
    """The newest reading Codex left in its sessions, and why there may be none."""

    read_at: float | None = None
    primary: RateLimit | None = None
    secondary: RateLimit | None = None
    plan: str = ""
    credits: float | None = None
    source: str = ""
    note: str = ""

    @property
    def known(self) -> bool:
        """Whether Codex reported a window this engine can show."""
        return self.primary is not None or self.secondary is not None


def read_usage(codex_dir: Path, *, newest: int = MAX_SESSIONS) -> CodexUsage:
    """The last rate-limit record under `codex_dir/sessions`, or the reason there is none.

    `codex_dir` is the Codex home (`$CODEX_HOME` or `~/.codex`), handed in
    rather than resolved here so a caller reading another machine's home, or a
    test's fixture, is answered from the folder it actually named.
    """
    sessions = Path(codex_dir) / "sessions"
    if not sessions.is_dir():
        return CodexUsage(note=t("No Codex session on this machine: {path} does not exist.", path=sessions))
    files = sorted(
        (path for path in sessions.rglob("rollout-*.jsonl") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not files:
        return CodexUsage(note=t("No Codex session on this machine: {path} holds no rollout.", path=sessions))
    for path in files[:newest]:
        record = _newest_record(path)
        if record is not None:
            return record
    return CodexUsage(
        note=t("No rate-limit reading in the Codex sessions examined ({count}).", count=len(files[:newest])),
    )


def _newest_record(path: Path) -> CodexUsage | None:
    """The newest line of `path` carrying a usable reading, or None.

    A line without its own moment falls back to the file's last write: the
    reading was written then, and a percentage with no age at all would be the
    one thing this module exists to avoid.
    """
    for raw in _tail_lines(path):
        if b"rate_limits" not in raw:
            continue
        record = _parse_line(raw, source=path.name)
        if record is None or not record.known:
            continue
        if record.read_at is None:
            try:
                record = replace(record, read_at=path.stat().st_mtime)
            except OSError:  # nexgen: allow reason="a file that just vanished leaves the reading as it is: the report only loses its age"
                return record
        return record
    return None


def _parse_line(raw: bytes, *, source: str) -> CodexUsage | None:
    """One session line as a reading: its moment, its two windows, its plan."""
    try:
        row = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:  # nexgen: allow reason="a line cut in half by a crash is one line less: the reading before it still answers"
        return None
    if not isinstance(row, dict):
        return None
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    limits = payload.get("rate_limits") or row.get("rate_limits")
    if not isinstance(limits, dict):
        return None
    credits = limits.get("credits") if isinstance(limits.get("credits"), dict) else {}
    balance = credits.get("balance")
    return CodexUsage(
        read_at=_epoch(row.get("timestamp")),
        primary=_limit(limits.get("primary")),
        secondary=_limit(limits.get("secondary")),
        plan=str(limits.get("plan_type") or limits.get("limit_id") or ""),
        credits=float(balance) if isinstance(balance, (int, float)) else None,
        source=source,
    )


def _limit(value) -> RateLimit | None:
    """One window as Codex reports it; a window without a percentage is no reading."""
    if not isinstance(value, dict):
        return None
    used = value.get("used_percent")
    if not isinstance(used, (int, float)):
        return None
    window = value.get("window_minutes")
    resets = value.get("resets_at")
    return RateLimit(
        used_percent=float(used),
        window_minutes=int(window) if isinstance(window, (int, float)) else None,
        resets_at=int(resets) if isinstance(resets, (int, float)) else None,
    )


def _epoch(stamp) -> float | None:
    """An ISO-8601 moment as epoch seconds, or None when it cannot be read."""
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except ValueError:  # nexgen: allow reason="a stamp the standard library refuses leaves the reading with no age, which the report says out loud"
        return None


def _tail_lines(path: Path, chunk: int = TAIL_CHUNK_BYTES, budget: int = TAIL_BUDGET_BYTES):
    """The lines of `path` from the last to the first, reading at most `budget` bytes.

    `carry` is the beginning of a line whose end is in the chunk just read: it
    belongs to the chunk before, so it is prepended to the next read instead of
    being answered as a line of its own.
    """
    with open(path, "rb") as handle:
        handle.seek(0, 2)
        position = handle.tell()
        start = max(0, position - budget)
        carry = b""
        while position > start:
            step = min(chunk, position - start)
            position -= step
            handle.seek(position)
            lines = (handle.read(step) + carry).split(b"\n")
            carry = lines.pop(0) if position > 0 else b""
            for line in reversed(lines):
                if line.strip():
                    yield line
