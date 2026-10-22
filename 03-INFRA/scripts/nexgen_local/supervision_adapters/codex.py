"""Codex as an adapter: the sandbox is the profile, the store is the transcript's index.

One job: open a Codex tab inside `workspace-write` with no approval prompts, send
one turn into a thread, read the thread's tokens and its last turn's status from
the stores Codex keeps under `~/.codex`, and resume by id. Codex has no project
permission file the engine can trust before the tab starts, so the push is
stopped by the sandbox (no network) and by the copy's own blocked pushurl, and
the capability table says exactly that instead of claiming a deny rule.
"""
from __future__ import annotations

import glob
import json
import os
import sqlite3
import time
from pathlib import Path

from nexgen_core.paths import resolve_home

from ..supervision_costs import SessionFacts, SessionMissing
from ..supervision_state import SupervisionError
from . import base

#: The sandbox every executor tab runs in: writes inside the workspace, no network.
SANDBOX = "workspace-write"
#: No approval prompts: outside the sandbox nothing is granted, it simply fails.
APPROVAL = "never"
#: Where Codex keeps its thread index and its turn history, versioned by name.
STATE_GLOB = "state_*.sqlite"
HISTORY_GLOB = "thread_history_*.sqlite"
SESSION_WAIT_S = 30.0
POLL_S = 2.0


class CodexAdapter(base.Adapter):
    """Codex CLI, bound to a thread id and to the folder it was born in."""

    name = "codex"
    label = "Codex"
    executable = "codex"
    capabilities = base.Capabilities(
        open_tab=True, find_session=True, send_turn=True, live_state=True, cost=False,
        tokens=True, write_permissions=False, resume=True, scout=True,
        session_model=True, last_turn=True,
        deny_push="sandbox workspace-write senza rete, nessuna approvazione, pushurl bloccata della copia",
    )

    def tab_argv(self, executable: str, folder: Path, prompt: str, *, session: str = "") -> list[str]:
        """`codex -C <folder> -s workspace-write -a never <pointer>`: a visible tab."""
        argv = [executable, "-C", str(folder), "-s", SANDBOX, "-a", APPROVAL]
        if session:
            argv.extend(["resume", session])
        argv.append(prompt)
        return argv

    def send_argv(self, executable: str, session: str, prompt: str, *, model: str = "") -> list[str]:
        """`codex exec -s workspace-write -a never resume <id> <pointer>`: one turn, no tab."""
        argv = [executable, "exec", "-s", SANDBOX, "-a", APPROVAL]
        if model:
            argv.extend(["-m", model])
        argv.extend(["resume", session, prompt])
        return argv

    def resume_argv(self, executable: str, session: str, folder: Path, prompt: str = "") -> list[str]:
        """`codex -s workspace-write -a never resume <id>`: the TUI on that thread."""
        argv = [executable, "-C", str(folder), "-s", SANDBOX, "-a", APPROVAL, "resume", session]
        if prompt:
            argv.append(prompt)
        return argv

    def find_session(self, folder: Path, since_ms: int, *, executable: str = "", expected: str = "",
                     wait_s: float = SESSION_WAIT_S, poll_s: float = POLL_S, clock=None,
                     sleep=None) -> base.SessionRef | None:
        """The newest thread of this folder created after the tab opened, read from the index."""
        now, pause = clock or time.monotonic, sleep or time.sleep
        deadline = now() + wait_s
        wanted = base.normal_path(folder)
        while True:
            found = self.threads(since_ms=since_ms, folder=wanted)
            if expected:
                found = [row for row in found if row["id"] == expected]
            if found:
                row = found[0]
                return base.SessionRef(
                    id=row["id"], folder=str(folder), title=str(row.get("title") or ""),
                    created_ms=int(row.get("created_ms") or 0),
                )
            if now() >= deadline:
                return None
            pause(poll_s)

    def threads(self, *, since_ms: int = 0, folder: str = "") -> list[dict]:
        """Threads from the index, newest first, filtered by creation time and folder."""
        path = _newest(STATE_GLOB)
        if path is None:
            raise base.AdapterError("archivio dei thread di Codex non trovato")
        try:
            con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            raise base.AdapterError(f"archivio dei thread non leggibile ({exc})") from exc
        try:
            rows = con.execute(
                "select id, cwd, model, tokens_used, created_at, updated_at, title "
                "from threads order by created_at desc limit 200"
            ).fetchall()
        except sqlite3.Error as exc:
            raise base.AdapterError(f"archivio dei thread non leggibile ({exc})") from exc
        finally:
            con.close()
        found = []
        for row in rows:
            created_ms = int(row[4] or 0) * 1000
            if since_ms and created_ms < since_ms:
                continue
            if folder and base.normal_path(row[1] or "") != folder:
                continue
            found.append({
                "id": str(row[0]), "folder": str(row[1] or ""), "model": str(row[2] or ""),
                "tokens_used": row[3], "created_ms": created_ms,
                "updated_ms": int(row[5] or 0) * 1000, "title": str(row[6] or ""),
            })
        return found

    def measure(self, session: str):
        """The thread's tokens and status; Codex reports no money, and the engine says so."""
        row = self._thread(session)
        turn = self._last_turn(session)
        running = turn["status"] == "in_progress"
        updated = int(row.get("updated_ms") or 0)
        return SessionFacts(
            model=str(row.get("model") or ""),
            cost_usd=None,
            tokens_total=_int_or_none(row.get("tokens_used")),
            outcome=turn["status"] or ("working" if running else "idle"),
            error_type=turn["error_type"],
            error_message=turn["error_message"],
            created_ms=int(row.get("created_ms") or 0) or None,
            updated_ms=updated or None,
            idle_ms=0 if running else (updated or None),
            cost_note="Codex non riporta il costo: valgono i token",
        )

    def session_model(self, session: str) -> str:
        """The model the thread index reports for this thread."""
        return str(self._thread(session).get("model") or "")

    def last_turn(self, session: str):
        """The last turn's status and the error it closed with."""
        from ..supervision_turns import Turn

        found = self._last_turn(session)
        return Turn(
            outcome="failed" if found["status"] == "failed" else found["status"],
            error_type=found["error_type"], error_message=found["error_message"],
            at_ms=found["completed_ms"] or found["started_ms"],
        )

    def scout_argv(self, executable: str, prompt: str) -> list[str]:
        """A reconnaissance round in the read-only sandbox, non-interactive."""
        return [executable, "exec", "-s", "read-only", "-a", APPROVAL, prompt]

    def _thread(self, session: str) -> dict:
        """One thread's index row, or a refusal that says the store lost it."""
        path = _newest(STATE_GLOB)
        if path is None:
            raise SessionMissing(f"archivio dei thread assente: il thread {session} non è leggibile")
        try:
            con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
            row = con.execute(
                "select id, cwd, model, tokens_used, created_at, updated_at, title "
                "from threads where id = ?", (session,),
            ).fetchone()
        except sqlite3.Error as exc:
            raise SupervisionError(f"archivio dei thread non leggibile ({exc})") from exc
        finally:
            con.close()
        if row is None:
            raise SessionMissing(f"il thread {session} non è nell'archivio di Codex")
        return {
            "id": str(row[0]), "folder": str(row[1] or ""), "model": str(row[2] or ""),
            "tokens_used": row[3], "created_ms": int(row[4] or 0) * 1000,
            "updated_ms": int(row[5] or 0) * 1000, "title": str(row[6] or ""),
        }

    def _last_turn(self, session: str) -> dict:
        """The last turn of a thread: status, its error, and when it started."""
        empty = {
            "status": "", "error_type": "", "error_message": "", "started_ms": 0, "completed_ms": 0,
        }
        path = _newest(HISTORY_GLOB)
        if path is None:
            return empty
        try:
            con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
            row = con.execute(
                "select status, error_json, started_at, completed_at from thread_turns "
                "where thread_id = ? order by started_at desc limit 1", (session,),
            ).fetchone()
        except sqlite3.Error:
            return empty
        finally:
            con.close()
        if row is None:
            return empty
        error = _error(row[1])
        return {
            "status": str(row[0] or ""), "error_type": error[0], "error_message": error[1],
            "started_ms": int(row[2] or 0) * 1000, "completed_ms": int(row[3] or 0) * 1000,
        }


def _error(raw) -> tuple[str, str]:
    """The error of a failed turn as type and message; an unreadable one names itself."""
    if not raw:
        return "", ""
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return "errore non leggibile", str(raw)[:300]
    if isinstance(parsed, dict):
        return str(parsed.get("type") or parsed.get("code") or "errore"), str(
            parsed.get("message") or parsed.get("error") or ""
        )[:300]
    return "errore", str(parsed)[:300]


def _newest(pattern: str) -> Path | None:
    """The newest store matching a versioned name, or None when Codex never wrote one."""
    home = Path(os.environ.get("CODEX_HOME") or (resolve_home() / ".codex"))
    found = sorted(glob.glob(str(home / pattern)), key=lambda item: Path(item).stat().st_mtime, reverse=True)
    return Path(found[0]) if found else None


def _int_or_none(value) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None
