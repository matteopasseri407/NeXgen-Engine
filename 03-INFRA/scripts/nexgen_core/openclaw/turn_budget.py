"""What one OpenClaw turn really spends: its model calls and the tokens they cost.

nexgen: one-job reason="measure model calls and tokens per turn from a harness transcript"

One job: group a Claude Code transcript into turns and say, per turn, how many model
calls it made and what those calls cost in input, cache read, cache write and output
tokens, so a session that passes the first-prompt budget and still spends millions of
tokens becomes visible.

Why it exists. `context_budget.py` measures the seed: one number per session. A turn
pays that seed on *every* model call, so on 2026-11-14 a `claude-cli` turn made about
74 calls of 78,717 tokens each -- 5.8 M tokens -- and a 150,000 first-prompt budget
reported it green (`docs/proposals/daru-burn.md`, sections 0 and 4). The bill is the
turn, not the seed.

Where a turn starts. Claude Code writes one JSONL record per streamed message, and a
genuine user prompt is a `user` record whose content carries no `tool_result`. A tool
result is written as a `user` record too, so counting every `user` record as a turn
would report 74 turns of one call each instead of one turn of 74 calls. A message
repeated by streaming is one call: it is counted once, by `message.id`.

Which agent owns a transcript. Claude Code names the project folder after the working
directory the session runs in: every character that is not a letter or a digit becomes
a dash, so `C:\\Users\\matte\\.openclaw\\workspace-capo` is the folder
`C--Users-matte--openclaw-workspace-capo`. An ACP agent runs in the `cwd` its runtime
declares, every other agent in its `workspace`, and both are readable from the
configuration in force. The ACP lane is therefore measured with no extra knowledge: it
is the newest session in that folder, which is where the harness itself writes.

agy is not measured: its CLI writes no usage a file reader can trust (the same reason
`context_budget.py` exempts it). Codex is measured by its first prompt only; this reader
groups Claude Code transcripts, which is the lane where a flat plan burns per call.
"""
from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nexgen_core.openclaw.context_budget import LOOKBACK_SECONDS, token_count

#: The per-turn ceiling, in tokens. Measured: one `claude-cli` model call on 2026-11-14
#: cost 78,853 tokens (2 input + 53,392 cache read + 25,323 cache write + 136 output),
#: and a 74-call turn of those is 5.8 M. 2,000,000 sits between a normal turn of a few
#: calls and that one, so a turn that reaches it is a turn somebody has to look at.
PER_TURN_BUDGET = 2_000_000

#: Environment variable to override the per-turn ceiling.
TURN_BUDGET_ENV_VAR = "NEXGEN_OPENCLAW_TURN_BUDGET"

#: How many of the newest turns of an agent's newest session are judged: enough to hold
#: the turn that just burned a window, without walking a whole day of history.
RECENT_TURNS = 20

#: How many bytes from the end of a transcript are read: 8 MiB, about 250 records of the
#: size a `claude-cli` call writes, which is more than the 20 turns judged above. The
#: doctor runs on a schedule, and a session that has grown for weeks must not make every
#: run read it from the first line.
TAIL_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class Turn:
    """One turn of one transcript: how many model calls it made and what they cost."""

    calls: int
    tokens: int


@dataclass(frozen=True)
class AgentTurn:
    """The most expensive recent turn of one agent, and the session it came from."""

    agent: str
    session: Path
    calls: int
    tokens: int


def get_turn_budget(env: Mapping[str, str] | None = None) -> int:
    """The per-turn ceiling in force: environment variable when set and valid, default otherwise."""
    raw = (env if env is not None else os.environ).get(TURN_BUDGET_ENV_VAR)
    if raw is not None:
        try:
            value = int(raw.strip())
            if value > 0:
                return value
        except ValueError:  # nexgen: allow reason="an unparseable budget falls back to the default rather than crashing"
            pass
    return PER_TURN_BUDGET


def resolved_budget(budget: int | None = None, env: Mapping[str, str] | None = None) -> int:
    """The ceiling to use: explicit argument if provided, environment or default otherwise."""
    if budget is not None and budget > 0:
        return budget
    return get_turn_budget(env)


def slug_for_cwd(cwd: str) -> str:
    """The `~/.claude/projects` folder name Claude Code gives one working directory.

    Every character that is not an ASCII letter or digit becomes a dash: Claude Code names
    the folder by the path, so `C:\\Users\\matte\\.openclaw\\workspace-capo` is written to
    `C--Users-matte--openclaw-workspace-capo` beside every other session of that folder.
    """
    return "".join(char if char.isascii() and char.isalnum() else "-" for char in str(cwd or "").strip())


def transcript_folder(claude_dir: Path, cwd: str) -> Path:
    """Where the harness writes the transcripts of the sessions it runs in `cwd`."""
    return claude_dir / "projects" / slug_for_cwd(cwd)


def newest_session(claude_dir: Path, cwd: str) -> Path | None:
    """The most recently written transcript of that folder, or None when there is none."""
    folder = transcript_folder(claude_dir, cwd)
    try:
        files = [path for path in folder.glob("*.jsonl") if path.is_file()]
    except OSError:  # nexgen: allow reason="an unreadable folder holds no transcript, never a crash"
        return None
    if not files:
        return None
    return max(files, key=lambda path: path.stat().st_mtime)


def _records(path: Path, offset: int = 0) -> Iterator[dict[str, Any]]:
    """Every JSON object a transcript line carries from `offset`, skipping half-written ones."""
    try:
        with path.open("rb") as handle:
            handle.seek(offset)
            if offset:
                handle.readline()  # a byte window that starts mid-line starts at the next one
            for raw in handle:
                clean = raw.decode("utf-8", "replace").strip()
                if not clean:
                    continue
                try:
                    entry = json.loads(clean)
                except ValueError:  # nexgen: allow reason="corrupted or half-written line is ignored so the next line can answer"
                    continue
                if isinstance(entry, dict):
                    yield entry
    except OSError:  # nexgen: allow reason="unreadable transcript is no measurement"
        return


def _window_start(path: Path) -> int:
    """Where reading starts: 0, or the byte that keeps a long transcript's cost bounded.

    The doctor runs on a schedule and only judges the newest turns, so a session that has
    grown for weeks is not re-read to the first line. The window can cut through a turn;
    `read_claude_turns` drops that turn rather than reporting a part of it.
    """
    try:
        return max(path.stat().st_size - TAIL_BYTES, 0)
    except OSError:  # nexgen: allow reason="a vanished transcript opens nothing"
        return 0


def _opens_a_turn(entry: Mapping[str, Any]) -> bool:
    """Whether this record is a person's prompt rather than a tool result handed back."""
    if entry.get("type") != "user":
        return False
    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return True
    if not isinstance(content, list):
        return False
    return not any(isinstance(block, dict) and block.get("type") == "tool_result" for block in content)


def _call_cost(usage: Mapping[str, Any]) -> int:
    """What one model call cost: the prompt it re-read plus what it wrote."""
    return (token_count(usage.get("input_tokens"))
            + token_count(usage.get("cache_read_input_tokens"))
            + token_count(usage.get("cache_creation_input_tokens"))
            + token_count(usage.get("output_tokens")))


def read_claude_turns(path: Path) -> list[Turn]:
    """Every turn of one Claude Code transcript, in order, with its calls and tokens."""
    turns: list[Turn] = []
    calls = 0
    tokens = 0
    offset = _window_start(path)
    # A whole file may open a turn implicitly, for a transcript with no prompt in it; a
    # window that starts mid-file may not: its first records belong to a turn already
    # under way, and reporting that part would be a wrong number, not a small one.
    opened = offset == 0
    answered: set[str] = set()
    for entry in _records(path, offset):
        if _opens_a_turn(entry):
            if opened and (calls or tokens):
                turns.append(Turn(calls=calls, tokens=tokens))
            calls, tokens, opened = 0, 0, True
            continue
        if entry.get("type") != "assistant" or not opened:
            continue
        message = entry.get("message")
        if not isinstance(message, dict):
            continue
        message_id = str(message.get("id") or "")
        if message_id and message_id in answered:
            continue
        usage = message.get("usage")
        if not isinstance(usage, dict):
            continue
        cost = _call_cost(usage)
        if cost <= 0:
            continue
        if message_id:
            answered.add(message_id)
        calls, tokens = calls + 1, tokens + cost
    if opened and (calls or tokens):
        turns.append(Turn(calls=calls, tokens=tokens))
    return turns


def agent_folders(config: Mapping[str, Any] | None) -> dict[str, str]:
    """Each configured agent's working folder: where its harness writes its transcripts.

    An ACP agent runs in the `cwd` its runtime block declares; every other agent runs in
    the folder its entry names as `workspace`. The ACP one wins when both are there,
    because that is the folder the harness process is started in.
    """
    agents = (config or {}).get("agents")
    entries = agents.get("entries") if isinstance(agents, Mapping) else None
    if not isinstance(entries, Mapping):
        return {}
    folders: dict[str, str] = {}
    for agent_id, entry in entries.items():
        if not isinstance(entry, Mapping):
            continue
        runtime = entry.get("runtime") if isinstance(entry.get("runtime"), Mapping) else {}
        acp = runtime.get("acp") if isinstance(runtime.get("acp"), Mapping) else {}
        folder = acp.get("cwd") if isinstance(acp.get("cwd"), str) and acp.get("cwd").strip() else entry.get("workspace")
        if isinstance(folder, str) and folder.strip():
            folders[str(agent_id)] = folder.strip()
    return folders


def worst_turn(
    claude_dir: Path,
    agent: str,
    cwd: str,
    *,
    limit: int = RECENT_TURNS,
    window_seconds: float = LOOKBACK_SECONDS,
    now: float | None = None,
) -> AgentTurn | None:
    """The most expensive of this agent's newest turns, or None when nothing was measured.

    The session has to have been written inside the look-back window: a turn from last
    week is history, and reporting it every day would make the line unreadable.
    """
    if not str(cwd or "").strip():
        return None
    session = newest_session(claude_dir, cwd)
    if session is None:
        return None
    curr = now if now is not None else time.time()
    try:
        if session.stat().st_mtime < curr - window_seconds:
            return None
    except OSError:  # nexgen: allow reason="a vanished transcript is no measurement"
        return None
    turns = read_claude_turns(session)[-limit:]
    if not turns:
        return None
    heaviest = max(turns, key=lambda turn: turn.tokens)
    return AgentTurn(agent=str(agent), session=session, calls=heaviest.calls, tokens=heaviest.tokens)


def agent_turns(
    claude_dir: Path,
    config: Mapping[str, Any] | None,
    *,
    limit: int = RECENT_TURNS,
    window_seconds: float = LOOKBACK_SECONDS,
    now: float | None = None,
) -> list[AgentTurn]:
    """The most expensive recent turn of every agent the configuration names, worst first."""
    spent: list[AgentTurn] = []
    for agent, cwd in agent_folders(config).items():
        reading = worst_turn(
            claude_dir, agent, cwd, limit=limit, window_seconds=window_seconds, now=now,
        )
        if reading is not None:
            spent.append(reading)
    return sorted(spent, key=lambda reading: reading.tokens, reverse=True)
