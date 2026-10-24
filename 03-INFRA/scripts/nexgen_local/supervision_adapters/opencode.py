"""OpenCode as an adapter: the behaviour the engine already had, in one place.

One job: build the command lines, read the session list and the store, write the
project profile and free the per-folder service that OpenCode keeps under its
shared server. The measurement and the permission profile keep their own
modules, so the code that was tested against the real harness stays the code that
runs: this adapter only decides when to call it and with which harness name.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

from nexgen_core.processes import windows_command_argv

from .. import supervision_costs as costs
from .. import supervision_permissions as permissions
from .. import supervision_processes as proces
from .. import supervision_turns as turns
from . import base

#: How long the harness gets to answer a question about its own sessions.
LIST_TIMEOUT_S = 120.0
API_TIMEOUT_S = 120.0
#: How long the service gets to release a location before its children are checked.
DISPOSE_WAIT_S = 1.5
#: How long a new tab gets to open its session before the attach is left manual.
SESSION_WAIT_S = 30.0
POLL_S = 2.0
#: Clock slack between this process and the harness's own timestamps.
CREATED_SLACK_MS = 5000


class OpenCodeAdapter(base.Adapter):
    """The harness the engine grew up with: one session per task, bound to a folder."""

    name = "opencode"
    label = "OpenCode"
    executable = "opencode"
    session_id_pattern = re.compile(r"ses_[A-Za-z0-9]{1,64}")
    session_id_description = "`ses_` seguito da lettere e cifre"
    capabilities = base.Capabilities(
        open_tab=True, find_session=True, send_turn=True, live_state=True, cost=True,
        tokens=True, write_permissions=True, resume=True, scout=True,
        dispose_location=True, session_model=True, last_turn=True, mcp=True,
        deny_push="profilo del progetto (opencode.json) e pushurl bloccata della copia",
    )

    def tab_argv(self, executable: str, folder: Path, prompt: str, *, session: str = "") -> list[str]:
        """`opencode <folder> [--session <id>] --prompt <pointer>`: never `--auto`."""
        argv = [executable, str(folder)]
        if session:
            argv.extend(["-s", session])
        argv.extend(["--prompt", prompt])
        return argv

    def send_argv(self, executable: str, session: str, prompt: str, *, model: str = "") -> list[str]:
        """`opencode run -s <id> [-m <model>] <pointer>`: one turn, no second tab."""
        argv = [executable, "run", "-s", session]
        if model:
            argv.extend(["-m", model])
        argv.append(prompt)
        return argv

    def resume_argv(self, executable: str, session: str, folder: Path, prompt: str = "") -> list[str]:
        """The TUI on the session the task already has, verified against OpenCode 2's `--session`."""
        return self.tab_argv(executable, folder, prompt, session=session)

    def find_session(self, folder: Path, since_ms: int, *, executable: str = "", wait_s: float = SESSION_WAIT_S,
                     lister=None, poll_s: float = POLL_S, clock=None, sleep=None,
                     expected: str = "") -> base.SessionRef | None:
        """The newest session opened in this folder at or after `since_ms`, or None."""
        wanted = base.normal_path(folder)
        list_sessions = lister or (lambda: self.list_sessions(folder, executable=executable))
        now, pause = clock or time.monotonic, sleep or time.sleep
        deadline = now() + wait_s
        while True:
            matches = [
                item for item in list_sessions()
                if base.normal_path(item.get("directory") or "") == wanted
                and int(item.get("created") or 0) >= since_ms - CREATED_SLACK_MS
            ]
            if matches:
                item = max(matches, key=lambda row: int(row.get("created") or 0))
                return base.SessionRef(
                    id=str(item.get("id") or ""), folder=str(item.get("directory") or ""),
                    title=str(item.get("title") or ""), created_ms=int(item.get("created") or 0),
                )
            if now() >= deadline:
                return None
            pause(poll_s)

    def list_sessions(self, folder: str | Path, *, executable: str = "", runner=None) -> list[dict]:
        """Every session the harness knows for this folder's project, newest first.

        `runner` is the seam the engine passes its own `subprocess.run` through, so
        the process is started where the caller expects and can replace it.
        """
        binary = executable or costs.opencode_executable()
        argv = windows_command_argv([binary, "session", "list", "--format", "json", "-n", "100"])
        try:
            done = (runner or subprocess.run)(
                argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                stdin=subprocess.DEVNULL, timeout=LIST_TIMEOUT_S, check=False, cwd=str(folder),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise base.AdapterError(
                f"`opencode session list` non eseguibile ({type(exc).__name__})"
            ) from exc
        if done.returncode:
            raise base.AdapterError(f"`opencode session list` esce con {done.returncode}")
        try:
            payload = json.loads(done.stdout)
        except ValueError as exc:
            raise base.AdapterError("`opencode session list` non è JSON") from exc
        if not isinstance(payload, list):
            raise base.AdapterError("`opencode session list` non è una lista di sessioni")
        return [item for item in payload if isinstance(item, dict)]

    def measure(self, session: str):
        """The export, plus the children the harness keeps in its own store."""
        return costs.measure(session)

    def session_model(self, session: str) -> str:
        """The model tag the export reports for this session."""
        return str(getattr(self.measure(session), "model", "") or "")

    def last_turn(self, session: str):
        """The store's last idle message, which says which provider error closed the turn."""
        return turns.last_turn(session)

    def write_profile(self, task_id: str, folder: Path, *, model: str = "",
                      models: dict[str, dict[str, dict]] | None = None,
                      mcp: list[str] | tuple[str, ...] | None = None) -> None:
        """`opencode.json` in the copy, with the project's other keys kept."""
        permissions.write_profile(task_id, folder, model=model, models=models, mcp=mcp)

    def scout_argv(self, executable: str, prompt: str) -> list[str]:
        """A read-only round through OpenCode's `plan` agent, never `--auto`."""
        return [executable, "run", "--agent", "plan", prompt]

    def dispose_location(self, folder: str | Path) -> tuple[bool, str]:
        """Free everything the shared server holds for this folder.

        The server exposes `debug.location.evict` for this, and on a server
        started for one client it releases the MCP servers of that directory.
        The shared `opencode serve --service` answers 204 and keeps them, because
        a service keeps its locations for whichever client attaches next; that
        is why the API's answer alone is not believed. The children of that
        server whose working directory is this folder are the processes that
        keep `git worktree remove` failing with "Device or resource busy", and
        they are stopped only after an active session in the folder has been
        ruled out.
        """
        folder = Path(folder)
        argv = windows_command_argv([
            "opencode", "api", "-H", f"x-opencode-directory:{folder}",
            "--param", f"location[directory]={folder}", "debug.location.evict",
        ])
        try:
            done = subprocess.run(
                argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                stdin=subprocess.DEVNULL, timeout=API_TIMEOUT_S, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise base.AdapterError(
                f"`opencode api debug.location.evict` non eseguibile ({type(exc).__name__})"
            ) from exc
        detail = ((done.stdout or "") + (done.stderr or "")).strip()[:300]
        refusal = f"`opencode api debug.location.evict` esce con {done.returncode}: {detail}"
        if done.returncode == 0:
            time.sleep(DISPOSE_WAIT_S)
        table, held = self._children_in(folder)
        if done.returncode == 0 and not held:
            return True, f"istanza del servizio liberata per {folder}"
        if done.returncode != 0 and not held:
            return False, refusal
        active = self._active_session_in(folder)
        if active:
            return False, (
                f"il servizio tiene {folder} per la sessione attiva {active}: "
                "non fermo i suoi processi"
            )
        stopped = self._stop(table, held)
        still = self._children_in(folder)[1]
        if still:
            return False, (
                f"il servizio tiene ancora {len(still)} processi legati a {folder} "
                f"dopo averne fermati {stopped}: la copia non è libera"
            )
        if done.returncode == 0:
            return True, (
                f"il servizio ha risposto 204 ma ha tenuto {folder}; "
                f"fermati {stopped} processi legati alla copia"
            )
        return True, f"l'API ha rifiutato ({refusal}); fermati {stopped} processi legati alla copia"

    def _children_in(self, folder: Path) -> tuple[list[dict], list[dict]]:
        """The process table and the shared server's children working in this folder."""
        server = self._server_pid()
        if not server:
            return [], []
        wanted = base.normal_path(folder)
        table = proces.rows()
        found: list[dict] = []
        for row in table:
            if row.get("ppid") != server:
                continue
            where = proces.cwd(row["pid"])
            if where is None:
                continue
            here = base.normal_path(where)
            if here == wanted or here.startswith(wanted + "/"):
                found.append(row)
        return table, found

    def _stop(self, table: list[dict], rows: list[dict]) -> int:
        """Stop these children and their own children, deepest first, and say how many."""
        targets = proces.subtree(table, [row["pid"] for row in rows])
        proces.kill([row["pid"] for _, row in targets])
        return len(targets)

    def _server_pid(self) -> int:
        """The pid of the server `opencode api` talks to, or 0 when it does not answer."""
        value = self._api_json(["GET", "/api/info"]).get("pid")
        return value if isinstance(value, int) and value > 0 else 0

    def _active_session_in(self, folder: Path) -> str:
        """The running session whose folder this is, or an empty string when there is none."""
        active = self._api_json(["GET", "/api/session/active"])
        rows = active.get("data") if isinstance(active, dict) else None
        if not isinstance(rows, dict):
            return ""
        wanted = base.normal_path(folder)
        for session, entry in rows.items():
            if not isinstance(entry, dict) or str(entry.get("type") or "") != "running":
                continue
            info = self._api_json(["GET", f"/api/session/{session}"])
            location = info.get("location") if isinstance(info, dict) else None
            where = str((location or {}).get("directory") or "") if isinstance(location, dict) else ""
            if where and base.normal_path(where) == wanted:
                return str(session)
        return ""

    def _api_json(self, request: list[str], *, executable: str = "") -> dict:
        """One read-only request through `opencode api`, parsed; an unreadable answer is empty."""
        argv = windows_command_argv([executable or self.executable, "api", *request])
        try:
            done = subprocess.run(
                argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                stdin=subprocess.DEVNULL, timeout=API_TIMEOUT_S, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):  # nexgen: allow reason="un servizio che non risponde non ha figli da attribuire: la lista resta vuota"
            return {}
        if done.returncode:
            return {}
        try:
            payload = json.loads(done.stdout or "{}")
        except ValueError:  # nexgen: allow reason="una risposta illeggibile non attribuisce processi: meglio nessun figlio che uno sbagliato"
            return {}
        return payload if isinstance(payload, dict) else {}

    def locations(self) -> list[str]:
        """Every directory the shared server currently holds, as its debug API lists them."""
        argv = windows_command_argv(["opencode", "api", "GET", "/api/debug/location"])
        try:
            done = subprocess.run(
                argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                stdin=subprocess.DEVNULL, timeout=API_TIMEOUT_S, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise base.AdapterError(
                f"`opencode api GET /api/debug/location` non eseguibile ({type(exc).__name__})"
            ) from exc
        if done.returncode:
            raise base.AdapterError(
                f"`opencode api GET /api/debug/location` esce con {done.returncode}"
            )
        try:
            payload = json.loads(done.stdout or "[]")
        except ValueError as exc:
            raise base.AdapterError("la lista delle cartelle del server non è JSON") from exc
        if not isinstance(payload, list):
            raise base.AdapterError("la lista delle cartelle del server non è una lista")
        return [
            str(item.get("directory") or "")
            for item in payload if isinstance(item, dict) and item.get("directory")
        ]
