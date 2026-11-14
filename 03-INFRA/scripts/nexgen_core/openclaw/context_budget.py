"""Context budget measurement for OpenClaw-started harness sessions.

nexgen: one-job reason="measure first-prompt token consumption from local harness transcripts and identify over-budget sessions"
One job: say how many tokens the first prompt of a Claude Code (or Codex) session
cost, read tolerantly from its local transcript, and report which recent OpenClaw-started
sessions went over a budget.

agy note: `agy` is NOT measured in tokens: its CLI writes no usage a file reader can
trust. No proxy is invented for it.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

#: Default budget for the first prompt of a session (measured: 77,840 tokens with
#: provider deny, 945,389 without it; 150,000 sits safely between the two).
FIRST_PROMPT_BUDGET = 150_000

#: Look-back window for recent sessions: 24 hours.
LOOKBACK_SECONDS = 24 * 3600.0

#: Environment variable to override the default budget.
BUDGET_ENV_VAR = "NEXGEN_OPENCLAW_FIRST_PROMPT_BUDGET"


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
    first assistant message. Streaming lines repeating the same message.id are counted once.
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
                inp = int(usage.get("input_tokens") or 0)
                creation = int(usage.get("cache_creation_input_tokens") or 0)
                read = int(usage.get("cache_read_input_tokens") or 0)
                return inp + creation + read
    except OSError:  # nexgen: allow reason="unreadable transcript file is treated as no reading"
        return None
    return None


def read_codex_first_prompt(path: Path) -> int | None:
    """The token count of the first prompt from a Codex session rollout JSONL, or None.

    Reads the first token_usage_record line and sums input_tokens and cache fields.
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
                    inp = int(usage.get("input_tokens") or 0)
                    cached = int(usage.get("cached_input_tokens") or 0)
                    cache_write = int(usage.get("cache_write_input_tokens") or 0)
                    total = inp + cached + cache_write
                    return total if total > 0 else (inp if inp > 0 else None)
    except OSError:  # nexgen: allow reason="unreadable codex rollout treated as no reading"
        return None
    return None


def find_recent_claude_sessions(
    claude_dir: Path,
    *,
    window_seconds: float = LOOKBACK_SECONDS,
    now: float | None = None,
) -> list[SessionReading]:
    """Every recent OpenClaw-started Claude Code session with its measured first prompt tokens."""
    projects_dir = claude_dir / "projects"
    if not projects_dir.is_dir():
        return []
    curr = now if now is not None else time.time()
    cutoff = curr - window_seconds
    readings: list[SessionReading] = []

    try:
        subdirs = [p for p in projects_dir.iterdir() if p.is_dir()]
    except OSError:  # nexgen: allow reason="unreadable projects dir returns empty list"
        return []

    for proj in subdirs:
        if not is_openclaw_slug(proj.name):
            continue
        try:
            files = [f for f in proj.glob("*.jsonl") if f.is_file()]
        except OSError:  # nexgen: allow reason="unreadable project subdir skipped"
            continue
        for session_file in files:
            try:
                mtime = session_file.stat().st_mtime
            except OSError:  # nexgen: allow reason="vanished file skipped"
                continue
            if mtime < cutoff:
                continue
            tokens = read_claude_first_prompt(session_file)
            if tokens is not None:
                readings.append(SessionReading(
                    slug=proj.name,
                    tokens=tokens,
                    path=session_file,
                    mtime=mtime,
                    harness="claude",
                ))
    return readings


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


def newest_openclaw_claude_session(
    claude_dir: Path,
    since: float,
    *,
    now: float | None = None,
) -> SessionReading | None:
    """The newest OpenClaw-started Claude Code transcript modified since `since`."""
    curr = now if now is not None else time.time()
    readings = find_recent_claude_sessions(claude_dir, window_seconds=max(curr - since + 1.0, 1.0), now=curr)
    filtered = [r for r in readings if r.mtime >= since - 0.5]
    if not filtered:
        return None
    return max(filtered, key=lambda r: r.mtime)
