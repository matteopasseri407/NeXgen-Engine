"""The Workboard as a mirror of the units: one card per unit, moved as the journal moves.

One job: create a unit's card when the copy is made and move it when the engine
changes state, through `openclaw workboard ... --json`, and never let the board
become the truth. The journal stays the state; a card that cannot be created or
moved is one line the owner reads, never an error that stops a task, and a machine
without OpenClaw simply has no board to mirror.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from . import supervision_policy as policy
from . import supervision_processes as processes
from . import supervision_state as state
from .supervision_state import SupervisionError

#: The journal event that says a unit got a card, and the one that says it moved.
CARD_EVENT = "workboard_card"
MOVE_EVENT = "workboard_moved"
#: How long the board gets to answer: one local SQLite write, or one refused plugin.
TIMEOUT_S = 20.0
#: How much of the brief the card carries: the pointer, not the brief.
BRIEF_CHARS = 400


def enabled() -> bool:
    """Whether this lane wants the cards, and whether a card may be written from here.

    A card is a write into the owner's own harness state, so the engine's test
    switch stops it before anything is looked up: a test that wants cards unsets
    it and puts a recorder in front of `openclaw`. A machine without the CLI has
    no board to mirror, and that is not worth a line on every unit.
    """
    if os.environ.get("NEXGEN_DISABLE_HOST_MUTATIONS") == "1":
        return False
    try:
        if not policy.workboard_enabled():
            return False
    except SupervisionError:  # nexgen: allow reason="una configurazione illeggibile non può accendere una scrittura nello stato del proprietario: senza una risposta certa la board resta spenta"
        return False
    return _executable() != ""


def card_of(task_id: str, unit: str) -> str:
    """The card this unit was given, as the journal recorded it; empty when it never got one."""
    name = state.slug(unit)
    found = ""
    for row in state.read_events(task_id):
        if row.get("event") == CARD_EVENT and str(row.get("unit") or "") == name and row.get("card"):
            found = str(row["card"])
    return found


def create(task_id: str, unit: str, record: dict) -> str:
    """Create the unit's card and return the one line the owner reads; empty when there is no board."""
    if not enabled():
        return ""
    name = str(record.get("unit") or state.slug(unit))
    try:
        agent = policy.openclaw_agent()
    except SupervisionError as exc:
        return f"scheda Workboard non creata: {exc}"
    argv = [
        _executable(), "workboard", "create", f"unità {name} · {task_id}",
        "--notes", _notes(task_id, name, record),
        "--status", "todo",
        "--labels", f"{task_id},{name}",
        "--agent", agent,
        "--json",
    ]
    done, problem = _ask(argv)
    if problem:
        return f"scheda Workboard non creata: {problem} (l'unità non dipende dalla scheda)"
    card = _card_id(done)
    if not card:
        return "scheda Workboard non creata: la risposta non porta l'identificativo della scheda"
    state.append_event(task_id, CARD_EVENT, unit=name, card=card, status="todo", agent=agent)
    return f"scheda Workboard {card[:8]} per l'unità {name} (agente {agent})"


def move(task_id: str, unit: str, status: str, *, reason: str = "") -> str:
    """Move the unit's card to one status and say so in one line; empty when there is no card."""
    card = card_of(task_id, unit)
    if not card or not enabled():
        return ""
    argv = [_executable(), "workboard", "move", card, "--status", status, "--json"]
    _payload, problem = _ask(argv)
    if problem:
        return f"scheda Workboard {card[:8]} non spostata a {status}: {problem}"
    name = state.slug(unit)
    state.append_event(task_id, MOVE_EVENT, unit=name, card=card, status=status)
    line = f"scheda Workboard {card[:8]} a {status}"
    return f"{line} ({reason})" if reason else line


def _notes(task_id: str, unit: str, record: dict) -> str:
    """What the card says: where the copy is, on which branch, and the brief in short."""
    folder = str(record.get("worktree") or "")
    brief = _brief_excerpt(Path(folder).parent / f"20-brief-{unit}.md") if folder else ""
    parts = [
        f"compito: {task_id}",
        f"unità: {unit}",
        f"copia: {folder}",
        f"ramo: {record.get('branch') or '(non dichiarato)'}",
    ]
    if brief:
        parts.append(f"brief in breve: {brief}")
    return "\n".join(parts)


def _brief_excerpt(path: Path) -> str:
    """The first lines of the brief that say something, cut short: the card points, it does not copy."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:  # nexgen: allow reason="una scheda senza il testo del brief resta utile: la copia e il ramo ci sono"
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return " ".join(lines)[:BRIEF_CHARS].strip()


def _ask(argv: list[str]) -> tuple[dict, str]:
    """One question to the board: the parsed answer, or the sentence saying why there is none."""
    try:
        done = processes.captured(argv, timeout=TIMEOUT_S)
    except processes.ProcessError as exc:
        return {}, str(exc)
    if done.returncode:
        detail = (done.stdout or "").strip().splitlines()
        first = detail[0][:200] if detail else ""
        return {}, f"`openclaw workboard` esce con {done.returncode}" + (f": {first}" if first else "")
    try:
        payload = json.loads(done.stdout or "null")
    except ValueError:
        return {}, "la risposta del Workboard non è JSON"
    return payload if isinstance(payload, dict) else {}, ""


def _card_id(payload: dict) -> str:
    """The card id from the shapes the CLI prints: the card itself, or the bare record."""
    card = payload.get("card") if isinstance(payload.get("card"), dict) else payload
    return str((card or {}).get("id") or "").strip()


def _executable() -> str:
    """`openclaw` where this machine keeps it, or an empty string when it is not installed."""
    return shutil.which("openclaw") or ""
