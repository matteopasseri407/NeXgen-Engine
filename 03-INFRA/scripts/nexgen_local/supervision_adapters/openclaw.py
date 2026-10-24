"""OpenClaw as an adapter: the owner's own agent, in the session the owner watches.

One job: build the `openclaw agent` turn, read the session the harness keeps for
it, and measure that session from the two places OpenClaw writes: the session
list (`sessions --json`) and the envelope each turn prints when it ends. There is
no terminal window of the engine here: OpenClaw shows the session in its Control
UI and on the owner's phone, which is the whole reason the engine drives it, so
the capability table says `window=False` and the engine starts a turn instead of
a tab.

Two facts decide the shape of this adapter, both read from the installed
documentation and the installed code:

- `openclaw agent` takes `--agent`, `--session-key` and `--message`, and has no
  `--cwd`: a gateway-backed turn runs in the workspace the owner configured for
  that agent. The unit's copy therefore has to be inside that workspace, and
  `check_folder` refuses before anything starts when it is not. A missing
  `--admin` scope is the reason an arbitrary folder cannot be forced, and the
  refusal names the clean way instead of pretending the turn happened elsewhere.
- The turn's numbers are not in the session list: the list carries tokens, model
  and the clock, and the per-run cost comes from the `--json` envelope. The
  engine redirects that envelope into `turn_log`, one line per turn, and
  `measure` adds the lines up. A turn that printed no cost leaves the number
  absent, never zero.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path

from .. import supervision_policy as policy
from .. import supervision_processes as processes
from .. import supervision_state as state
from ..supervision_costs import SessionFacts, SessionMissing
from ..supervision_turns import Turn
from . import base

#: How long the harness gets to answer a question about its own sessions.
LIST_TIMEOUT_S = 120.0
#: How long the gateway gets to accept the archive of a finished session.
CLOSE_TIMEOUT_S = 30.0
#: A session is in the list as soon as its turn is admitted; this is the grace for that.
SESSION_WAIT_S = 30.0
POLL_S = 2.0
#: Where one turn's own envelope is kept, under the lane's state, one line per turn.
LOG_DIR = "openclaw"
#: What a session key may hold: the shape OpenClaw prints, and nothing a shell could split.
_KEY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9:._-]{0,180}")
#: What a turn status means, in the engine's words.
OUTCOMES = {"ok": "completed", "error": "failed", "timeout": "failed"}


class OpenClawAdapter(base.Adapter):
    """OpenClaw, driven by the session key the engine chooses and OpenClaw shows."""

    name = "openclaw"
    label = "OpenClaw"
    executable = "openclaw"
    capabilities = base.Capabilities(
        open_tab=True, window=False, find_session=True, send_turn=True, live_state=True,
        cost=True, tokens=True, write_permissions=False, resume=True, scout=False,
        dispose_location=False, session_model=True, last_turn=True,
        deny_push="pushurl bloccata della copia; OpenClaw non ha un profilo di progetto che il motore scriva",
        limits=(
            "nessuna finestra propria: il turno si guarda nella Control UI; l'agente gira nel suo "
            "workspace, quindi la copia deve starci dentro"
        ),
    )

    def new_session_id(self, task_id: str = "", unit: str = "") -> str:
        """The key this unit's session will have, chosen here so finding it is not a race.

        OpenClaw routes `--agent <id> --session-key <key>` to `agent:<id>:<key>`,
        and the Control UI shows that key: naming it after the task and the unit
        makes the session recognizable without a lookup table.
        """
        if task_id and unit:
            return f"sup-{state.slug(f'{task_id}-{unit}', limit=80)}"
        return f"sup-{uuid.uuid4().hex[:12]}"

    def agent(self) -> str:
        """The OpenClaw agent a unit's turn runs on, from the lane's settings."""
        return policy.openclaw_agent()

    def tab_argv(self, executable: str, folder: Path, prompt: str, *, session: str = "") -> list[str]:
        """The same turn as `send_argv`: there is no window of the engine to open."""
        return self.send_argv(executable, session, prompt)

    def send_argv(self, executable: str, session: str, prompt: str, *, model: str = "") -> list[str]:
        """`openclaw agent --agent <id> --session-key <key> --message <pointer> --json`."""
        key = self.session_key(session)
        argv = [
            executable, "agent", "--agent", self.agent(), "--session-key", key,
            "--message", prompt, "--json",
        ]
        if model:
            argv.extend(["--model", model])
        return argv

    def resume_argv(self, executable: str, session: str, folder: Path, prompt: str = "") -> list[str]:
        """A second turn in the same session: there is no TUI to reopen."""
        return self.send_argv(executable, session, prompt)

    def session_key(self, session: str) -> str:
        """The key as OpenClaw accepts it: one token, refused before it reaches an argument."""
        key = (session or "").strip()
        if not _KEY_RE.fullmatch(key):
            raise base.AdapterError(
                f"chiave di sessione OpenClaw non valida: {session!r} "
                "(attese lettere, cifre e `:._-`)"
            )
        return key

    def check_folder(self, folder: Path) -> None:
        """Refuse a copy outside the agent's workspace, and name the clean way.

        The gateway runs a turn in the workspace configured for the agent; the
        documented CLI offers no flag for another folder, and a host path outside
        the configured workspaces needs `operator.admin`. Forcing it would leave
        a session that looks like work in the unit and is not, so the copy either
        is inside the workspace or the turn does not start.
        """
        agent = self.agent()
        workspace = self.workspace_of(agent)
        wanted = base.normal_path(folder)
        root = base.normal_path(workspace)
        if wanted == root or wanted.startswith(root + "/"):
            return
        raise base.AdapterError(
            f"la copia {folder} è fuori dal workspace dell'agente OpenClaw {agent} ({workspace}): "
            "il turno girerebbe lì e non nell'unità. Strada pulita: punta "
            f"`agents.entries.{agent}.workspace` sulla cartella del compito, oppure lascia che la "
            "scheda del Workboard avvii il turno (`openclaw workboard dispatch`, `--admin` per una "
            "cartella fuori dai workspace configurati)"
        )

    def workspace_of(self, agent: str = "") -> Path:
        """The folder OpenClaw says this agent works in, asked to the harness itself."""
        wanted = (agent or self.agent()).strip()
        known = self.agents()
        for entry in known:
            if str(entry.get("id") or "").strip() != wanted:
                continue
            folder = str(entry.get("workspace") or "").strip()
            if not folder:
                raise base.AdapterError(f"l'agente OpenClaw {wanted} non dichiara un workspace")
            return Path(folder)
        names = ", ".join(str(entry.get("id") or "") for entry in known) or "nessuno"
        raise base.AdapterError(f"agente OpenClaw sconosciuto: {wanted!r} (configurati: {names})")

    def agents(self, *, executable: str = "") -> list[dict]:
        """Every agent OpenClaw lists, with the workspace each one runs in."""
        payload = self._json([(executable or self.executable), "agents", "list", "--json"])
        if not isinstance(payload, list):
            raise base.AdapterError("`openclaw agents list --json` non è una lista di agenti")
        return [item for item in payload if isinstance(item, dict)]

    def find_session(self, folder: Path, since_ms: int, *, executable: str = "", expected: str = "",
                     wait_s: float = SESSION_WAIT_S, poll_s: float = POLL_S, clock=None,
                     sleep=None) -> base.SessionRef | None:
        """The session this key opened, once OpenClaw's own list shows it."""
        key = self.session_key(expected)
        now, pause = clock or time.monotonic, sleep or time.sleep
        deadline = now() + wait_s
        while True:
            row = self.session_row(key, executable=executable, missing_ok=True)
            if row is not None:
                return base.SessionRef(
                    id=str(row.get("key") or key), folder=str(folder),
                    title=str(row.get("label") or key), created_ms=_created_ms(row),
                )
            if now() >= deadline:
                return None
            pause(poll_s)

    def sessions(self, *, executable: str = "") -> list[dict]:
        """Every session OpenClaw keeps, as its own `sessions --json` prints them."""
        payload = self._json([(executable or self.executable), "sessions", "--json"])
        rows = payload.get("sessions") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise base.AdapterError("`openclaw sessions --json` non è un elenco di sessioni")
        return [item for item in rows if isinstance(item, dict)]

    def session_row(self, session: str, *, executable: str = "", missing_ok: bool = False) -> dict | None:
        """This session's row, or the harness's own "it is gone" when the list has none."""
        key = self.session_key(session)
        for row in self.sessions(executable=executable):
            name = str(row.get("key") or "")
            if name == key or name.endswith(":" + key) or str(row.get("sessionId") or "") == key:
                return row
        if missing_ok:
            return None
        raise SessionMissing(f"la sessione OpenClaw {key} non è nell'elenco del harness")

    def measure(self, session: str):
        """The session's tokens, model and clock, plus the money its turns printed."""
        row = self.session_row(session)
        turns = self._turns(session)
        running = self.running(session)
        updated = _int_or_none(row.get("updatedAt")) or 0
        tokens_in, tokens_out = _ints(row)
        total = _int_or_none(row.get("totalTokens"))
        cost = _sum_cost(turns)
        if tokens_in is None and tokens_out is None:
            tokens_in, tokens_out = _sum_usage(turns)
        note = ""
        if cost is None and (total or tokens_in or tokens_out):
            note = "OpenClaw non riporta il costo di questi turni: valgono i token"
        last = _last_envelope(turns)
        return SessionFacts(
            model=self._model(row),
            cost_usd=cost,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            tokens_total=total,
            outcome="working" if running else _outcome(last, row),
            error_type=str((last.get("error") or {}).get("type") or "") if last else "",
            error_message=str((last.get("error") or {}).get("message") or "")[:300] if last else "",
            created_ms=_created_ms(row) or None,
            updated_ms=updated or None,
            idle_ms=0 if running else (updated or None),
            cost_note=note,
        )

    def session_model(self, session: str) -> str:
        """The model the session list reports for this session."""
        return self._model(self.session_row(session))

    def last_turn(self, session: str):
        """The last envelope this session's own turn printed, or its stored status."""
        row = self.session_row(session)
        last = _last_envelope(self._turns(session))
        if last:
            return Turn(
                outcome=_outcome(last, row),
                error_type=str((last.get("error") or {}).get("type") or ""),
                error_message=str((last.get("error") or {}).get("message") or "")[:300],
                at_ms=_int_or_none(row.get("updatedAt")) or 0,
            )
        status = str(row.get("status") or "")
        return Turn(outcome="failed" if status == "failed" else status)

    def running(self, session: str) -> bool:
        """Whether an `openclaw` process on this machine still carries this turn's key.

        The turn is a CLI process, and the session list says nothing about who is
        working right now: the process table is the one place that does.
        """
        key = self.session_key(session)
        try:
            rows = processes.rows()
        except processes.ProcessError:  # nexgen: allow reason="una tabella di processi illeggibile non prova che il turno sia finito"
            return False
        for row in rows:
            command = str(row.get("command_line") or "")
            if key in command and "openclaw" in command.lower():
                return True
        return False

    def turn_log(self, session: str) -> Path:
        """One line per turn, under the lane's state: the only place the cost survives."""
        name = re.sub(r"[^A-Za-z0-9._-]+", "-", self.session_key(session))
        return state.lane_state_dir() / LOG_DIR / f"{name}.jsonl"

    def close_session(self, session: str) -> str:
        """Archive the session OpenClaw keeps for this unit; a refusal is one line, never a raise."""
        key = self.session_key(session)
        try:
            done = processes.captured([self.executable, "sessions", "archive", key, "--json"], timeout=CLOSE_TIMEOUT_S)
        except processes.ProcessError as exc:
            return f"sessione OpenClaw {key} non archiviata: {exc}"
        if done.returncode:
            detail = (done.stdout or "").strip()[:200]
            return f"sessione OpenClaw {key} non archiviata: `openclaw sessions archive` esce con {done.returncode} ({detail})"
        return f"sessione OpenClaw {key} archiviata: non resta aperta nel Control UI"

    def _model(self, row: dict) -> str:
        """`provider/model` as the session list names them, when both are there."""
        provider = str(row.get("modelProvider") or "").strip()
        model = str(row.get("model") or "").strip()
        if provider and model and "/" not in model:
            return f"{provider}/{model}"
        return model

    def _turns(self, session: str) -> list[dict]:
        """Every envelope this session's turns left behind, in the order they ended."""
        path = self.turn_log(session)
        if not path.is_file():
            return []
        found: list[dict] = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                parsed = json.loads(line)
            except ValueError:  # nexgen: allow reason="una riga di envelope illeggibile non annulla le altre righe del turno"
                continue
            if isinstance(parsed, dict):
                found.append(parsed)
        return found

    def _json(self, argv: list[str]):
        """One read-only question to the harness, parsed; an unanswerable one is a refusal."""
        try:
            done = processes.captured(argv, timeout=LIST_TIMEOUT_S)
        except processes.ProcessError as exc:
            raise base.AdapterError(str(exc)) from exc
        if done.returncode:
            detail = (done.stdout or "").strip()[:300]
            raise base.AdapterError(f"`{' '.join(argv[:2])}` esce con {done.returncode}: {detail}")
        try:
            return json.loads(done.stdout or "null")
        except ValueError as exc:
            raise base.AdapterError(f"`{' '.join(argv[:2])}` non è JSON") from exc


def _created_ms(row: dict) -> int:
    """When this session started, from the two clocks the list may carry."""
    return _int_or_none(row.get("sessionStartedAt")) or _int_or_none(row.get("updatedAt")) or 0


def _ints(row: dict) -> tuple[int | None, int | None]:
    """The token counts the session list carries, when it carries them."""
    return _int_or_none(row.get("inputTokens")), _int_or_none(row.get("outputTokens"))


def _int_or_none(value) -> int | None:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _envelope(line: dict) -> dict:
    """The documented fields of one turn, whichever of the two envelopes wrote them.

    A gateway turn answers with the Gateway's own object and its run statistics
    under `meta.agentMeta`; an isolated `agent exec` answers with the flat
    envelope. Both are documented, so both are read, and a field neither carries
    stays absent.
    """
    meta = line.get("meta") if isinstance(line.get("meta"), dict) else {}
    agent_meta = meta.get("agentMeta") if isinstance(meta.get("agentMeta"), dict) else {}
    error = line.get("error") if isinstance(line.get("error"), dict) else None
    if error is None and isinstance(meta.get("error"), dict):
        error = meta["error"]
    return {
        "status": str(line.get("status") or ""),
        "ok": line.get("ok"),
        "cost_usd": _float(line.get("costUsd")) if line.get("costUsd") is not None
        else _float(agent_meta.get("costUsd")),
        "usage": agent_meta.get("usage") if isinstance(agent_meta.get("usage"), dict)
        else (line.get("usage") if isinstance(line.get("usage"), dict) else {}),
        "error": error,
    }


def _read_turns(turns: list[dict]) -> list[dict]:
    """The envelopes of a session, read once into the shape `measure` asks for."""
    return [_envelope(turn) for turn in turns]


def _last_envelope(turns: list[dict]) -> dict:
    """The envelope of the last turn, or an empty one when the session printed none."""
    read = _read_turns(turns)
    return read[-1] if read else {}


def _sum_cost(turns: list[dict]) -> float | None:
    """What this session's turns printed as cost, or None when none printed one."""
    costs = [item["cost_usd"] for item in _read_turns(turns) if item["cost_usd"] is not None]
    return round(sum(costs), 6) if costs else None


def _sum_usage(turns: list[dict]) -> tuple[int | None, int | None]:
    """The token counts the envelopes carry, when the session list carries none."""
    read = _read_turns(turns)
    pairs = [(_int_or_none(item["usage"].get("input")), _int_or_none(item["usage"].get("output")))
             for item in read]
    pairs = [pair for pair in pairs if pair != (None, None)]
    if not pairs:
        return None, None
    return sum(pair[0] or 0 for pair in pairs), sum(pair[1] or 0 for pair in pairs)


def _outcome(envelope: dict, row: dict) -> str:
    """How the last turn closed, in the engine's words, when the session is not running."""
    status = str(envelope.get("status") or "").strip().lower()
    if status in OUTCOMES:
        return OUTCOMES[status]
    if envelope.get("ok") is True:
        return "completed"
    if envelope.get("error"):
        return "failed"
    stored = str(row.get("status") or "").strip()
    return "failed" if stored == "failed" else "idle"


def _float(value) -> float | None:
    """A cost as the envelope printed it; anything else is absent, never zero."""
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
