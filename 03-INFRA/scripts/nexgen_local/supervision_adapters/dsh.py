"""DeepSeek Harness (`dsh`) as an adapter: one headless turn per call, the model in a patch.

One job: build the `dsh --profile headless --patch <file> --json` command for a
unit's first turn and for every later one (`--session-id`), write the per-call
patch that pins the model the supervisor chose, and read the NDJSON stream each
turn leaves in its own log back into a session id, its tokens and how it ended.
The launcher, the route, the credential and the stream format are the shared
`nexgen_core.dsh_cli`, the same code the Council's dsh seat runs.

The harness is a developer preview and any release candidate can change its
flags or its stream: every shape this adapter does not recognise is a refusal
with the reason, never a guess. A model the harness does not have is the
harness's own `UNKNOWN_MODEL` refusal (verified live on 0.2.2-rc.5), so a wrong
name never falls back to another model, and a route without a key is refused
here before anything starts. Sessions live in the person's own `~/.dsh`, so the
desktop app shows every unit's session in its workspace.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path

from nexgen_core import dsh_cli

from .. import supervision_processes as processes
from .. import supervision_state as state
from ..supervision_costs import SessionFacts, SessionMissing
from ..supervision_turns import Turn
from . import base

#: The model a unit runs on when nobody chose one: the cheap flat lane, never a dear default.
DEFAULT_MODEL = "opencode-go/deepseek-v4.1-flash"
#: Where every turn's NDJSON, patch and launcher live, under the lane's state.
LOG_DIR = "dsh-turns"
#: The first line this adapter writes into a turn's log, before the harness writes its own.
REQUEST_TYPE = "nexgen-request"
#: The permission preset for a worker, and no prompt that nobody is there to answer.
PERMISSION_MODE = "workspace-write"
APPROVAL_POLICY = "never"
SESSION_WAIT_S = 60.0
POLL_S = 1.0


class DshAdapter(base.Adapter):
    """DeepSeek Harness, always headless: the app shows the session, the engine opens no window."""

    name = "dsh"
    label = "DeepSeek Harness"
    executable = "dsh"
    session_id_pattern = re.compile(r"session-[0-9a-fA-F-]{36}|dsh-[0-9a-fA-F-]{36}")
    session_id_description = "`session-` seguito da un UUID"
    capabilities = base.Capabilities(
        open_tab=False, window=False, headless=True, find_session=True, send_turn=True,
        live_state=True, tokens=True, resume=True, session_model=True, last_turn=True,
        deny_push="solo pushurl bloccata della copia (preset workspace-write, approvazioni negate)",
        limits=(
            "developer preview: ogni RC può cambiare flag e formato; riporta i token, non il costo; "
            "su Windows (0.2.2-rc.5) il sandbox workspace-write non scrive nel .git del checkout "
            "nemmeno con --add-dir: il file arriva nella copia, il commit no"
        ),
    )

    def new_session_id(self, task_id: str = "", unit: str = "") -> str:
        """The key of the first turn's log: dsh picks the session id itself and prints it there."""
        return f"dsh-{uuid.uuid4()}"

    def locate(self) -> str:
        """The launcher on PATH, or where the Windows installer keeps it inside the app."""
        return dsh_cli.executable() or ""

    def turn_env(self, model: str = "") -> dict[str, str]:
        """The route's key under the one variable the harness reads."""
        provider, _ = _route(model or DEFAULT_MODEL)
        return {dsh_cli.HARNESS_KEY_ENV: _credential(provider)}

    def headless_argv(self, executable: str, folder: Path, prompt: str, *, session: str = "",
                      model: str = "") -> list[str]:
        """The first turn: a new session in the copy, the task folder and git dir writable."""
        key = session or self.new_session_id()
        return self._argv(key, prompt, model or DEFAULT_MODEL, folder, resume="")

    def send_argv(self, executable: str, session: str, prompt: str, *, model: str = "") -> list[str]:
        """One more turn into the session, on the model it already runs unless another is asked."""
        events = self._events(session)
        if not events:
            raise SessionMissing(f"dsh non ha mai riportato la sessione {session}")
        folder = Path(_session_cwd(events) or ".")
        chosen = model or _requested_model(events) or DEFAULT_MODEL
        return self._argv(session, prompt, chosen, folder, resume=session)

    def resume_argv(self, executable: str, session: str, folder: Path, prompt: str = "") -> list[str]:
        """There is no tab to resume in: the next turn is the resume."""
        return self.send_argv(executable, session, prompt)

    def turn_log(self, session: str) -> Path:
        """One NDJSON file per turn, named after the key or the session it went into."""
        return self._log(session)

    def find_session(self, folder: Path, since_ms: int, *, executable: str = "", expected: str = "",
                     wait_s: float = SESSION_WAIT_S, poll_s: float = POLL_S, clock=None,
                     sleep=None) -> base.SessionRef | None:
        """The session the first turn's log announces, once dsh has printed it."""
        now, pause = clock or time.monotonic, sleep or time.sleep
        deadline = now() + wait_s
        while True:
            events = dsh_cli.read_events(_read(self._log(expected))) if expected else []
            found = dsh_cli.session_id(events)
            if found:
                return base.SessionRef(id=found, folder=_session_cwd(events) or str(folder), title=found)
            if now() >= deadline:
                return None
            pause(poll_s)

    def measure(self, session: str):
        """The session's tokens and model, and whether a turn of it is still running."""
        events = self._events(session)
        if not events:
            raise SessionMissing(f"dsh non ha mai riportato la sessione {session}")
        updated = self._updated_ms(session)
        final = dsh_cli.final_event(events)
        running = self.running(session)
        error = _error(final)
        return SessionFacts(
            model=_requested_model(events),
            tokens_total=dsh_cli.total_tokens(events) or None,
            cost_note="DeepSeek Harness non riporta il costo: valgono i token",
            outcome="working" if running else _outcome(final),
            updated_ms=updated,
            idle_ms=None if running else updated,
            error_type=error[0], error_message=error[1],
        )

    def session_model(self, session: str) -> str:
        """The model the last turn of this session asked for in its patch."""
        return _requested_model(self._events(session))

    def last_turn(self, session: str):
        """How the session's last turn closed, from the `final` event dsh printed."""
        final = dsh_cli.final_event(self._last_events(session))
        error = _error(final)
        return Turn(outcome=_outcome(final), error_type=error[0], error_message=error[1],
                    at_ms=self._updated_ms(session) or 0)

    def running(self, session: str) -> bool:
        """Whether a dsh process still carries the patch of one of this session's turns."""
        names = [path.with_suffix(".patch.yml").name for path in self._logs(session)]
        try:
            table = processes.rows()
        except processes.ProcessError:  # nexgen: allow reason="una tabella dei processi illeggibile non dice che il turno lavora: vale il log"
            return False
        return any(name in str(row.get("command_line") or "") for row in table for name in names)

    def _argv(self, key: str, prompt: str, model: str, folder: Path, *, resume: str) -> list[str]:
        """Write the patch and the request line for this turn, then the command that uses them."""
        provider, model_id = _route(model)
        _credential(provider)
        log = self._log(key if not resume else f"{resume}-{uuid.uuid4().hex[:8]}")
        patch = log.with_suffix(".patch.yml")
        log.parent.mkdir(parents=True, exist_ok=True)
        patch.write_text(dsh_cli.patch_text(dsh_cli.patch_document(provider, model_id)), encoding="utf-8")
        request = {"type": REQUEST_TYPE, "model": f"{provider}/{model_id}", "session": resume, "key": key}
        log.write_text(json.dumps(request) + "\n", encoding="utf-8")
        self._next_log = log
        argv = [dsh_cli.launcher(log.parent, _write), "--profile", dsh_cli.HEADLESS_PROFILE,
                "--patch", str(patch)]
        if resume:
            argv.extend(["--session-id", resume])
        argv.extend(["--permission-mode", PERMISSION_MODE, "--approval-policy", APPROVAL_POLICY])
        for root in base.writable_roots(folder):
            argv.extend(["--add-dir", str(root)])
        argv.extend(["--json", prompt])
        return argv

    def _log(self, name: str) -> Path:
        """The log of the turn about to start, or the file of a key named before."""
        pending = getattr(self, "_next_log", None)
        if pending is not None and (pending.stem == name or pending.stem.startswith(f"{name}-")):
            return pending
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", name or "turn")
        return state.lane_state_dir() / LOG_DIR / f"{safe}.ndjson"

    def _logs(self, session: str) -> list[Path]:
        """Every turn log that belongs to this session, oldest first."""
        folder = state.lane_state_dir() / LOG_DIR
        if not folder.is_dir():
            return []
        found = [
            path for path in folder.glob("*.ndjson")
            if session in _read(path)
        ]
        return sorted(found, key=lambda path: path.stat().st_mtime)

    def _events(self, session: str) -> list[dict]:
        """The events of every turn of this session, request lines included, in order."""
        return [event for path in self._logs(session) for event in dsh_cli.read_events(_read(path))]

    def _last_events(self, session: str) -> list[dict]:
        """The events of the session's newest turn only."""
        logs = self._logs(session)
        return dsh_cli.read_events(_read(logs[-1])) if logs else []

    def _updated_ms(self, session: str) -> int | None:
        """When the newest turn of this session last wrote its log."""
        logs = self._logs(session)
        return int(logs[-1].stat().st_mtime * 1000) if logs else None


def _route(model: str) -> tuple[str, str]:
    """`provider/model` split, or the refusal that names what was asked."""
    try:
        return dsh_cli.split_route(model)
    except dsh_cli.DshError as exc:
        raise base.AdapterError(f"modello dsh non valido {model!r}: serve <provider>/<modello>") from exc


def _credential(provider: str) -> str:
    """The key for this route, or a refusal: a turn without one would only fail later, slower."""
    value = dsh_cli.credential_value(provider)
    if not value:
        names = " o ".join(dsh_cli.credential_names(provider))
        raise base.AdapterError(
            f"nessuna chiave per la corsia {provider}: imposta {names} (ambiente o deposito dei segreti)"
        )
    return value


def _requested_model(events: list[dict]) -> str:
    """The model the newest request line of these events asked for."""
    asked = [str(event.get("model") or "") for event in events if event.get("type") == REQUEST_TYPE]
    return asked[-1] if asked else ""


def _session_cwd(events: list[dict]) -> str:
    """The folder dsh says the session works in."""
    for event in events:
        if event.get("type") == "session" and event.get("cwd"):
            return str(event["cwd"])
    return ""


def _outcome(final: dict | None) -> str:
    """`completed`, `failed`, or empty when the turn never printed its end."""
    if not final:
        return ""
    reason = final.get("reason") if isinstance(final.get("reason"), dict) else {}
    return "completed" if reason.get("kind") == "completed" else "failed"


def _error(final: dict | None) -> tuple[str, str]:
    """The error code and message of a turn that ended without an answer."""
    reason = (final or {}).get("reason")
    error = reason.get("error") if isinstance(reason, dict) else None
    if not isinstance(error, dict):
        return "", ""
    return str(error.get("code") or ""), str(error.get("message") or "")[:300]


def _read(path: Path) -> str:
    """A log's text, or empty while dsh has not written it yet."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:  # nexgen: allow reason="un log non ancora scritto è un turno che non ha detto niente, non un errore"
        return ""


def _write(path: Path, text: str) -> None:
    """The launcher wrapper's writer: a plain file in the lane's own state."""
    path.write_text(text, encoding="utf-8")
