"""Context budget measurement for OpenClaw-started harness sessions.

nexgen: one-job reason="measure first-prompt token consumption from local harness transcripts and identify over-budget sessions"
One job: say how many tokens the first prompt of a Claude Code (or Codex) session
cost, read tolerantly from its local transcript, and report which recent OpenClaw-started
sessions went over a budget.

It also owns the one walk of the Claude Code transcript folder, so "which sessions are
recent" (the doctor) and "which transcript belongs to this turn" (the smoke) can never
disagree. A turn's transcript is dated by its own first record: OpenClaw agents write
continuously, and the newest touched file is usually somebody else's chat.

agy note: `agy` is NOT measured in tokens: its CLI writes no usage a file reader can
trust. No proxy is invented for it.
"""
from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

#: Default budget for the first prompt of a session (measured: 77,840 tokens with
#: provider deny, 945,389 without it; 150,000 sits safely between the two).
FIRST_PROMPT_BUDGET = 150_000

#: Look-back window for recent sessions: 24 hours.
LOOKBACK_SECONDS = 24 * 3600.0

#: Environment variable to override the default budget.
BUDGET_ENV_VAR = "NEXGEN_OPENCLAW_FIRST_PROMPT_BUDGET"

#: How many opening records are read to date a session: the first line is the one
#: Claude Code writes, and a half-written or untimestamped one must not hide it.
FIRST_RECORDS_READ = 5
#: Slack when deciding whether a transcript started with a turn.
#:
#: A record's timestamp is written to the microsecond while the caller's clock has more
#: digits, and a turn that was told to start at T can legitimately open a session at
#: T-0.0000004. Half a second decides that without ever admitting an hour-old chat.
START_SLACK_SECONDS = 0.5


@dataclass(frozen=True)
class SessionReading:
    """Token measurement for the first prompt of one harness session."""

    slug: str
    tokens: int
    path: Path
    mtime: float
    harness: str = "claude"


def get_first_prompt_budget(env: Mapping[str, str] | None = None) -> int:
    """The budget in force: environment variable when set and valid, default otherwise."""
    raw = (env if env is not None else os.environ).get(BUDGET_ENV_VAR)
    if raw is not None:
        try:
            val = int(raw.strip())
            if val > 0:
                return val
        except ValueError:  # nexgen: allow reason="an unparseable budget falls back to the default rather than crashing"
            pass
    return FIRST_PROMPT_BUDGET


def resolved_budget(budget: int | None = None, env: Mapping[str, str] | None = None) -> int:
    """The budget to use: explicit argument if provided, environment or default otherwise."""
    if budget is not None and budget > 0:
        return budget
    return get_first_prompt_budget(env)


def is_openclaw_slug(slug_or_path: str) -> bool:
    """Whether a directory slug or path indicates an OpenClaw workspace."""
    s = str(slug_or_path).lower()
    return "openclaw" in s


def read_claude_first_prompt(path: Path) -> int | None:
    """The token sum of the first assistant message in a Claude Code JSONL transcript, or None.

    Sums input_tokens, cache_creation_input_tokens, and cache_read_input_tokens on the
    first assistant message: in a Claude transcript those three are separate, and the
    input one excludes what the cache served. Streaming lines repeating the same
    message.id are counted once.
    """
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                raw_clean = raw.strip()
                if not raw_clean:
                    continue
                try:
                    entry = json.loads(raw_clean)
                except ValueError:  # nexgen: allow reason="corrupted or half-written line is ignored so the next line can answer"
                    continue
                if not isinstance(entry, dict) or entry.get("type") != "assistant":
                    continue
                msg = entry.get("message")
                if not isinstance(msg, dict):
                    continue
                usage = msg.get("usage")
                if not isinstance(usage, dict):
                    continue
                return (token_count(usage.get("input_tokens"))
                        + token_count(usage.get("cache_creation_input_tokens"))
                        + token_count(usage.get("cache_read_input_tokens")))
    except OSError:  # nexgen: allow reason="unreadable transcript file is treated as no reading"
        return None
    return None


def read_codex_first_prompt(path: Path) -> int | None:
    """The token count of the first prompt from a Codex session rollout JSONL, or None.

    Codex reports `input_tokens` already containing `cached_input_tokens` (a real
    rollout on this engine: 41,715 input, 33,408 of them cached, 41,804 total with 89
    output), so adding the cached count again invents up to a whole prompt. Only the
    input side and a separate cache-write count are summed.
    """
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                raw_clean = raw.strip()
                if not raw_clean:
                    continue
                try:
                    entry = json.loads(raw_clean)
                except ValueError:  # nexgen: allow reason="corrupt line ignored"
                    continue
                if not isinstance(entry, dict):
                    continue
                if entry.get("type") == "token_usage_record":
                    payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
                    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
                    inp = token_count(usage.get("input_tokens"))
                    cache_write = token_count(usage.get("cache_write_input_tokens"))
                    total = inp + cache_write
                    return total if total > 0 else None
    except OSError:  # nexgen: allow reason="unreadable codex rollout treated as no reading"
        return None
    return None


def token_count(value: Any) -> int:
    """A token field as a count: anything that is not a number is not a measurement.

    The module promises a tolerant read, and one unusual line (a future CLI version, or
    a third-party writer sharing the folder) used to raise out of the report and become
    a crashed doctor check. `turn_budget.py` reads the same fields per turn, so the guard
    is here once instead of twice.
    """
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def claude_home(home: Path | None = None) -> Path:
    """Claude Code's own folder: `$CLAUDE_CONFIG_DIR` wins when set, else `~/.claude`.

    Claude Code honours that variable, so a machine that redirects it writes its
    transcripts somewhere this reader must look too.
    """
    from nexgen_core.paths import resolve_home

    override = os.environ.get("CLAUDE_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    return resolve_home(home) / ".claude"


def _openclaw_transcripts(claude_dir: Path) -> Iterator[tuple[Path, float]]:
    """Every OpenClaw Claude Code transcript under this folder, with its modification time.

    The one directory walk this module owns: the doctor asks it for the sessions of a
    window and the smoke asks it for the session that started with its turn, so the
    two can never disagree about which files exist.
    """
    projects_dir = claude_dir / "projects"
    if not projects_dir.is_dir():
        return
    try:
        subdirs = [p for p in projects_dir.iterdir() if p.is_dir() and is_openclaw_slug(p.name)]
    except OSError:  # nexgen: allow reason="unreadable projects dir yields no transcript, never a crash"
        return
    for proj in subdirs:
        try:
            files = [f for f in proj.glob("*.jsonl") if f.is_file()]
        except OSError:  # nexgen: allow reason="unreadable project subdir skipped"
            continue
        for session_file in files:
            try:
                mtime = session_file.stat().st_mtime
            except OSError:  # nexgen: allow reason="vanished file skipped"
                continue
            yield session_file, mtime


def find_recent_claude_sessions(
    claude_dir: Path,
    *,
    window_seconds: float = LOOKBACK_SECONDS,
    now: float | None = None,
) -> list[SessionReading]:
    """Every recent OpenClaw-started Claude Code session with its measured first prompt tokens."""
    curr = now if now is not None else time.time()
    cutoff = curr - window_seconds
    readings: list[SessionReading] = []
    for session_file, mtime in _openclaw_transcripts(claude_dir):
        if mtime < cutoff:
            continue
        tokens = read_claude_first_prompt(session_file)
        if tokens is not None:
            readings.append(SessionReading(
                slug=session_file.parent.name,
                tokens=tokens,
                path=session_file,
                mtime=mtime,
                harness="claude",
            ))
    return readings


def session_started_at(path: Path) -> float | None:
    """When this transcript wrote its first timestamped record, or None.

    Claude Code stamps every record it writes, so the first line dates the session
    itself rather than the last time somebody appended to it.
    """
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for _, raw in zip(range(FIRST_RECORDS_READ), f):
                try:
                    entry = json.loads(raw.strip())
                except ValueError:  # nexgen: allow reason="a half-written first line is skipped so the next record can date the session"
                    continue
                stamp = entry.get("timestamp") if isinstance(entry, dict) else None
                if not isinstance(stamp, str):
                    continue
                try:
                    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
                except ValueError:  # nexgen: allow reason="a timestamp this reader cannot parse is not a start time, and the next record may carry one"
                    continue
    except OSError:  # nexgen: allow reason="unreadable transcript dates no session"
        return None
    return None


def transcript_started_since(claude_dir: Path, since: float) -> SessionReading | None:
    """The OpenClaw transcript that started most recently at or after `since`, or None.

    A turn's own session is dated by its first record, never by the file's modification
    time: OpenClaw agents write continuously, so the newest touched file belongs to
    whichever chat wrote last, and measuring that one reports somebody else's tokens.
    The start time also orders candidates, because two files written in the same clock
    tick carry the same modification time and only their own records tell them apart.
    """
    best: SessionReading | None = None
    best_started = 0.0
    for session_file, mtime in _openclaw_transcripts(claude_dir):
        started = session_started_at(session_file)
        if started is None or started < since - START_SLACK_SECONDS:
            continue
        if best is not None and (started, mtime) <= (best_started, best.mtime):
            continue
        best = SessionReading(
            slug=session_file.parent.name, tokens=0, path=session_file, mtime=mtime, harness="claude",
        )
        best_started = started
    return best


def find_recent_codex_sessions(
    codex_dir: Path,
    *,
    window_seconds: float = LOOKBACK_SECONDS,
    now: float | None = None,
) -> list[SessionReading]:
    """Every recent OpenClaw-started Codex session with its measured first prompt tokens."""
    sessions_dir = codex_dir / "sessions"
    if not sessions_dir.is_dir():
        return []
    curr = now if now is not None else time.time()
    cutoff = curr - window_seconds
    readings: list[SessionReading] = []

    try:
        files = [p for p in sessions_dir.rglob("rollout-*.jsonl") if p.is_file()]
    except OSError:  # nexgen: allow reason="unreadable codex sessions dir returns empty list"
        return []

    for rollout in files:
        try:
            mtime = rollout.stat().st_mtime
        except OSError:  # nexgen: allow reason="vanished file skipped"
            continue
        if mtime < cutoff:
            continue
        is_oc = False
        try:
            with rollout.open("r", encoding="utf-8", errors="replace") as f:
                first_line = f.readline()
                if first_line:
                    meta = json.loads(first_line)
                    if isinstance(meta, dict) and meta.get("type") == "session_meta":
                        payload = meta.get("payload") if isinstance(meta.get("payload"), dict) else {}
                        cwd = payload.get("cwd") or ""
                        if is_openclaw_slug(str(cwd)):
                            is_oc = True
        except (OSError, ValueError):  # nexgen: allow reason="corrupted metadata header skipped"
            continue

        if not is_oc:
            continue

        tokens = read_codex_first_prompt(rollout)
        if tokens is not None:
            readings.append(SessionReading(
                slug=rollout.stem,
                tokens=tokens,
                path=rollout,
                mtime=mtime,
                harness="codex",
            ))
    return readings
