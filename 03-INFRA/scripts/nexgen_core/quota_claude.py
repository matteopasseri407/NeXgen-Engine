"""How many tokens Claude Code spent in the last five hours and seven days.

One job: add up `message.usage` from the transcripts Claude Code writes under
`~/.claude/projects`, per window, and say nothing about a percentage. Claude
Code keeps no plan usage in any local file, so the limit is not readable here
and is not guessed: this module reports what was consumed, and the report says
the rest out loud.

Two facts shaped the code, both measured on a live machine on 2026-10-24. The
same assistant message is written to a transcript several times with identical
usage (twenty copies of one reply), so summing lines inflates the total about
sixfold and the reader keys on `message.id`. And a week of transcripts is
hundreds of megabytes, so the bytes of a line are searched for `"usage"` before
any of it is parsed.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from nexgen_core.i18n import t

#: The windows the report shows: a stable key and how far back it reaches.
WINDOWS = (("5h", 5 * 3600), ("7d", 7 * 86400))

#: The token counters a message carries, all of them consumption of some kind.
TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")

#: A transcript last written before the widest window cannot hold a message
#: inside it. The margin absorbs a clock that was put right after the write.
MTIME_MARGIN_SECONDS = 3600


@dataclass(frozen=True)
class ClaudeUsage:
    """Tokens per window from the transcripts, or why there is no reading."""

    read_at: float | None = None
    windows: dict[str, dict] = field(default_factory=dict)
    source: str = ""
    note: str = ""

    @property
    def known(self) -> bool:
        """Whether any message was found at all."""
        return any(window.get("messages") for window in self.windows.values())


def read_usage(claude_dir: Path, *, now: float | None = None) -> ClaudeUsage:
    """Tokens per window under `claude_dir/projects`, or the reason there are none.

    `claude_dir` is Claude Code's home (`~/.claude`), handed in rather than
    resolved here so a caller reading another machine's home, or a test's
    fixture, is answered from the folder it actually named.
    """
    moment = time.time() if now is None else float(now)
    projects = Path(claude_dir) / "projects"
    if not projects.is_dir():
        return ClaudeUsage(
            read_at=moment,
            note=t("No Claude Code transcript on this machine: {path} does not exist.", path=projects),
        )
    totals = {key: {"tokens": 0, "messages": 0} for key, _ in WINDOWS}
    counted: set[str] = set()
    files = _recent_files(projects, moment)
    for path in files:
        for tokens, stamp, key in _messages(path):
            if stamp is None or stamp < moment - WINDOWS[-1][1]:
                continue
            if key:
                if key in counted:
                    continue
                counted.add(key)
            for window, seconds in WINDOWS:
                if stamp >= moment - seconds:
                    totals[window]["tokens"] += tokens
                    totals[window]["messages"] += 1
    note = ""
    if not any(window["messages"] for window in totals.values()):
        note = t("No Claude Code message in the last {days} days.", days=WINDOWS[-1][1] // 86400)
    return ClaudeUsage(
        read_at=moment,
        windows=totals,
        source=f"{len(files)} .jsonl",
        note=note,
    )


def _recent_files(projects: Path, moment: float) -> list[Path]:
    """Transcripts last written inside the widest window, oldest first.

    A file whose last write is older than the window cannot contain a message
    inside it, so the walk never opens a year of history to answer for a week.
    """
    cutoff = moment - WINDOWS[-1][1] - MTIME_MARGIN_SECONDS
    found: list[Path] = []
    for path in projects.rglob("*.jsonl"):
        try:
            if path.is_file() and path.stat().st_mtime >= cutoff:
                found.append(path)
        except OSError:  # nexgen: allow reason="a transcript that vanished mid-walk is one file less, not a failed reading"
            continue
    return sorted(found)


def _messages(path: Path):
    """Every message in `path` as (tokens, moment, message id), in file order."""
    with open(path, "rb") as handle:
        for raw in handle:
            if b'"usage"' not in raw:
                continue
            parsed = _parse(raw)
            if parsed is not None:
                yield parsed


def _parse(raw: bytes):
    """One transcript line as a usage reading, or None when it is not one."""
    try:
        row = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:  # nexgen: allow reason="a line cut in half by a crash is one line less: the rest of the transcript still answers"
        return None
    if not isinstance(row, dict):
        return None
    message = row.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("usage"), dict):
        return None
    usage = message["usage"]
    tokens = sum(_count(usage.get(name)) for name in TOKEN_FIELDS)
    if tokens <= 0:
        return None
    return tokens, _epoch(row.get("timestamp")), str(message.get("id") or "")


def _count(value) -> int:
    """One token counter as a whole number; anything else counts as nothing."""
    return int(value) if isinstance(value, (int, float)) and value > 0 else 0


def _epoch(stamp) -> float | None:
    """An ISO-8601 moment as epoch seconds, or None when it cannot be read."""
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except ValueError:  # nexgen: allow reason="a stamp the standard library refuses leaves that message out of every window, which is what the report already says"
        return None
