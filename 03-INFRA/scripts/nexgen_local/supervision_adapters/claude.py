"""Claude Code as an adapter: the id is chosen here, the store is read where it lives.

One job: open a tab on a session the engine names (`--session-id`), send one turn
into it, read the transcript Claude Code writes under `~/.claude/projects`, and
write the project permission profile before the tab starts. The session id is a
UUID the engine picks, so finding the session is not a race; whether the tab is
still working is read from the process table, because the transcript has no live
status and guessing one would queue work on a session that is done.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path

from nexgen_core.paths import resolve_home

from .. import supervision_permissions as permissions
from .. import supervision_processes as processes
from ..supervision_costs import SessionFacts, SessionMissing
from ..supervision_state import open_task
from ..supervision_turns import Turn
from . import base

#: Where Claude Code keeps one transcript per session, one folder per project.
PROJECTS_DIR = "projects"
#: The project file the executor runs under, kept out of the unit's diff.
SETTINGS_FILE = ".claude/settings.json"
#: A session that is not in the transcript yet gets this long to appear.
SESSION_WAIT_S = 30.0
POLL_S = 2.0
#: The permission mode: edits are accepted, every other request still asks.
PERMISSION_MODE = "acceptEdits"
#: The push, in the spellings a shell accepts, denied before it can run.
PUSH_DENY = ("Bash(git push *)", "Bash(git -C * push *)", "Bash(git -c * push *)")


class ClaudeAdapter(base.Adapter):
    """Claude Code, driven by an id the engine owns."""

    name = "claude"
    label = "Claude Code"
    executable = "claude"
    capabilities = base.Capabilities(
        open_tab=True, find_session=True, send_turn=True, live_state=True, cost=True,
        tokens=True, write_permissions=True, resume=True, scout=True,
        session_model=True, last_turn=True,
        deny_push="profilo di progetto .claude/settings.json e pushurl bloccata della copia",
    )

    def new_session_id(self) -> str:
        """The id the engine hands to `--session-id`, so finding the session is not a bet."""
        return str(uuid.uuid4())

    def tab_argv(self, executable: str, folder: Path, prompt: str, *, session: str = "") -> list[str]:
        """`claude --session-id <uuid> --permission-mode acceptEdits <pointer>`."""
        argv = [executable]
        if session:
            argv.extend(["--session-id", session])
        argv.extend(["--permission-mode", PERMISSION_MODE, prompt])
        return argv

    def send_argv(self, executable: str, session: str, prompt: str, *, model: str = "") -> list[str]:
        """`claude -p --resume <id> <pointer>`: one turn, printed, no tab."""
        argv = [executable, "-p", "--resume", session]
        if model:
            argv.extend(["--model", model])
        argv.extend(["--permission-mode", PERMISSION_MODE, prompt])
        return argv

    def resume_argv(self, executable: str, session: str, folder: Path, prompt: str = "") -> list[str]:
        """`claude --resume <id> [<pointer>]`: the TUI on the session the task already has."""
        argv = [executable, "--resume", session]
        if prompt:
            argv.append(prompt)
        return argv

    def find_session(self, folder: Path, since_ms: int, *, executable: str = "", expected: str = "",
                     wait_s: float = SESSION_WAIT_S, poll_s: float = POLL_S, clock=None,
                     sleep=None) -> base.SessionRef | None:
        """The transcript of the id the engine chose, once Claude Code has written it."""
        if not expected:
            raise base.AdapterError(
                "claude non sa trovare una sessione senza l'identificativo deciso dal motore"
            )
        now, pause = clock or time.monotonic, sleep or time.sleep
        deadline = now() + wait_s
        while True:
            path = self.session_file(expected)
            if path is not None:
                return base.SessionRef(
                    id=expected, folder=str(folder), created_ms=int(path.stat().st_mtime * 1000),
                )
            if now() >= deadline:
                return None
            pause(poll_s)

    def session_file(self, session: str) -> Path | None:
        """The transcript of this session, wherever this Claude Code version filed it."""
        root = self.projects_root()
        if not root.is_dir():
            return None
        for candidate in root.glob(f"*/{session}.jsonl"):
            return candidate
        return None

    def projects_root(self) -> Path:
        """`~/.claude/projects`, or the tree `CLAUDE_CONFIG_DIR` names."""
        base_dir = os.environ.get("CLAUDE_CONFIG_DIR") or str(resolve_home() / ".claude")
        return Path(base_dir) / PROJECTS_DIR

    def measure(self, session: str):
        """The transcript's cost state, its tokens, and whether a Claude process still holds it."""
        path = self.session_file(session)
        if path is None:
            raise SessionMissing(f"nessun transcript per la sessione {session}")
        found = _scan(path)
        running = self.running(session)
        updated = found["updated_ms"] or int(path.stat().st_mtime * 1000)
        created = found["created_ms"] or updated
        return SessionFacts(
            model=found["model"],
            cost_usd=found["cost_usd"],
            tokens_in=found["tokens_in"],
            tokens_out=found["tokens_out"],
            tokens_reasoning=found["tokens_reasoning"],
            tokens_cache_read=found["tokens_cache_read"],
            tokens_cache_write=found["tokens_cache_write"],
            outcome=found["outcome"] or ("working" if running else "idle"),
            error_type=found["error_type"],
            error_message=found["error_message"],
            created_ms=created,
            updated_ms=updated,
            idle_ms=0 if running else updated,
            cost_note=found["cost_note"],
        )

    def running(self, session: str) -> bool:
        """Whether a Claude Code process on this machine still carries the session id."""
        try:
            rows = processes.rows()
        except processes.ProcessError:  # nexgen: allow reason="una tabella di processi illeggibile non prova che la sessione sia viva"
            return False
        for row in rows:
            name = Path(str(row.get("name") or "")).name.lower()
            if name.startswith("claude") and session in str(row.get("command_line") or ""):
                return True
        return False

    def session_model(self, session: str) -> str:
        """The model of the last assistant message, or the last model the cost state names."""
        path = self.session_file(session)
        if path is None:
            raise SessionMissing(f"nessun transcript per la sessione {session}")
        return _scan(path)["model"]

    def last_turn(self, session: str):
        """The last assistant record, when it carries a provider error."""
        return _last_turn(self.session_file(session))

    def write_profile(self, task_id: str, folder: Path, *, model: str = "") -> None:
        """The project settings: edits accepted inside the copy and the task folder, push denied."""
        target = Path(folder) / SETTINGS_FILE
        if permissions.tracked(folder, SETTINGS_FILE):
            raise base.AdapterError(
                f"il progetto traccia {SETTINGS_FILE}: il profilo dell'esecutore non può stare lì "
                "senza sporcare il diff"
            )
        current = permissions.read_config(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        current["permissions"] = _permissions(folder, open_task(task_id))
        permissions.atomic_json(target, current)
        permissions.ignore(folder, SETTINGS_FILE)

    def scout_argv(self, executable: str, prompt: str) -> list[str]:
        """A read-only reconnaissance round: `--permission-mode plan` and no tab."""
        return [executable, "-p", "--permission-mode", "plan", prompt]


def _permissions(folder: Path, task_folder: Path) -> dict:
    """Allow edits where the unit works, deny the push in its spellings, accept edits by default."""
    return {
        "defaultMode": PERMISSION_MODE,
        "allow": ["Edit", "Write", "Read", "Bash(git *)"],
        "deny": list(PUSH_DENY),
        "additionalDirectories": [str(Path(task_folder).resolve())],
    }


def _scan(path: Path) -> dict:
    """One transcript: the last cost state, the last model, the closing error, the clock."""
    found = {
        "cost_usd": None, "tokens_in": None, "tokens_out": None, "tokens_reasoning": None,
        "tokens_cache_read": None, "tokens_cache_write": None, "model": "", "outcome": "",
        "error_type": "", "error_message": "", "cost_note": "",
        "created_ms": None, "updated_ms": None,
    }
    for row in _rows(path):
        _apply_clock(found, row)
        if row.get("type") == "cost-state":
            _apply_cost(found, row)
        kind = row.get("type")
        message = row.get("message") if isinstance(row.get("message"), dict) else {}
        if kind == "assistant" and message.get("model"):
            found["model"] = str(message["model"])
        error = message.get("error") if isinstance(message.get("error"), dict) else None
        if row.get("isApiErrorMessage") or error:
            found["outcome"] = "failed"
            found["error_type"] = str((error or {}).get("type") or "api_error")
            found["error_message"] = str((error or {}).get("message") or "")[:300]
    return found


def _apply_clock(found: dict, row: dict) -> None:
    """The transcript's first and last instant, from the rows and the cost state alike."""
    moment = _iso_ms(row.get("timestamp"))
    if not moment and row.get("type") == "cost-state":
        moment = _int_or_none(row.get("startTime"))
        if moment:
            found["updated_ms"] = max(found["updated_ms"] or 0, moment)
    if not moment:
        return
    found["created_ms"] = min(found["created_ms"] or moment, moment)
    found["updated_ms"] = max(found["updated_ms"] or 0, moment)


def _apply_cost(found: dict, row: dict) -> None:
    """The cumulative cost and the token sums the last cost state reports."""
    cost = row.get("totalCostUSD")
    found["cost_usd"] = round(float(cost), 6) if isinstance(cost, (int, float)) else None
    usage = row.get("modelUsage") if isinstance(row.get("modelUsage"), dict) else {}
    sums = {"input": 0, "output": 0, "thinking": 0, "cache_read": 0, "cache_write": 0}
    seen = False
    for model, entry in usage.items():
        if not isinstance(entry, dict):
            continue
        seen = True
        found["model"] = str(model)
        sums["input"] += _int_or_none(entry.get("inputTokens")) or 0
        sums["output"] += _int_or_none(entry.get("outputTokens")) or 0
        sums["thinking"] += _int_or_none(entry.get("thinkingTokens")) or 0
        sums["cache_read"] += _int_or_none(entry.get("cacheReadInputTokens")) or 0
        sums["cache_write"] += _int_or_none(entry.get("cacheCreationInputTokens")) or 0
    if seen:
        found["tokens_in"] = sums["input"]
        found["tokens_out"] = sums["output"]
        found["tokens_reasoning"] = sums["thinking"]
        found["tokens_cache_read"] = sums["cache_read"]
        found["tokens_cache_write"] = sums["cache_write"]
    if found["cost_usd"] is None and seen:
        found["cost_note"] = "Claude Code non riporta il costo di questo piano: valgono i token"


def _rows(path: Path) -> list[dict]:
    """Every record of a transcript; a line that does not parse is skipped, not fatal."""
    rows: list[dict] = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                parsed = json.loads(line)
            except ValueError:  # nexgen: allow reason="una riga della trascrizione non è JSON: le altre righe dicono il costo"
                continue
            if isinstance(parsed, dict):
                rows.append(parsed)
    return rows


def _last_turn(path: Path | None):
    """The last assistant record as a turn: failed only when it carries an error."""
    if path is None:
        return Turn()
    last = Turn()
    for row in _rows(path):
        if row.get("type") != "assistant":
            continue
        message = row.get("message") if isinstance(row.get("message"), dict) else {}
        error = message.get("error") if isinstance(message.get("error"), dict) else {}
        failed = bool(row.get("isApiErrorMessage") or error)
        last = Turn(
            outcome="failed" if failed else "completed",
            error_type=str(error.get("type") or ""),
            error_message=str(error.get("message") or "")[:300],
            at_ms=_iso_ms(row.get("timestamp")) or last.at_ms,
        )
    return last


def _iso_ms(value) -> int:
    """An ISO instant from the transcript in milliseconds; an unreadable one is zero."""
    if not isinstance(value, str) or not value:
        return 0
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:  # nexgen: allow reason="un istante illeggibile nella trascrizione vale zero, e zero non scarta nessun turno"
        return 0


def _int_or_none(value) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None
