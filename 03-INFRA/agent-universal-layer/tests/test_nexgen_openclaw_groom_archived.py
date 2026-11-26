"""Il groom non chiede mai di compattare una sessione che OpenClaw ha archiviato.

nexgen: one-job reason="riprodurre il guasto di OpenClaw 2026.11.10: le archiviate nella lista, il rifiuto di compact, il job spento"

Da 2026.11.10 `openclaw sessions list --all-agents --json` elenca anche le
sessioni archiviate senza dirlo, e `sessions.compact` le rifiuta con
"changed before compaction. Retry.": ogni giro falliva su una e lo scheduler
ha spento il job dopo dieci. Lo store SQLite qui è vero, con lo schema letto
sulla 2026.11.10; il gateway è finto.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import i18n  # noqa: E402
from nexgen_core.openclaw import sessions_archived, sessions_groom  # noqa: E402
from nexgen_core.openclaw.groom_policy import IDLE_SECONDS  # noqa: E402

NOW = 200_000.0 * 1000.0
ARCHIVED = "agent:capo:parity-vecchia"
LIVE = "agent:main:main"
#: La risposta di `openclaw sessions compact --json` 2026.11.10 su una sessione archiviata.
REFUSAL = json.dumps({"ok": False, "error": {
    "code": "INVALID_REQUEST", "message": f"Session {ARCHIVED} changed before compaction. Retry."}})


@pytest.fixture(autouse=True)
def _english_lines():
    i18n.set_language("en")
    yield
    i18n.set_language(None)


def _store(home: Path, agent: str, rows: list[tuple[str, int | None]], *, schema: str = "") -> Path:
    """Lo store di un agente dove OpenClaw lo tiene, con le sole colonne che il filtro legge."""
    path = home / ".openclaw" / "agents" / agent / "agent" / "openclaw-agent.sqlite"
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as db:
        db.execute(schema or "CREATE TABLE session_nodes (session_key TEXT PRIMARY KEY, archived_at INTEGER)")
        if not schema:
            db.executemany("INSERT INTO session_nodes VALUES (?, ?)", rows)
    return path


def _idle(key: str, tokens: int) -> dict:
    agent = key.split(":")[1]
    return {"key": key, "agentId": agent, "totalTokens": tokens, "updatedAt": NOW,
            "lastInteractionAt": NOW - (IDLE_SECONDS + 120) * 1000.0, "status": "done"}


class Gateway:
    """La CLI come la vede il groom: elenca anche le archiviate e rifiuta di compattarle."""

    def __init__(self, sessions):
        self.sessions = sessions
        self.compactions: list[str] = []

    def __call__(self, argv, **_kwargs):
        if argv[2] == "list":
            return subprocess.CompletedProcess(argv, 0, json.dumps({"sessions": self.sessions}), "")
        self.compactions.append(argv[3])
        if argv[3] == ARCHIVED:
            return subprocess.CompletedProcess(argv, 1, REFUSAL, "")
        return subprocess.CompletedProcess(argv, 0, '{"ok": true, "compacted": true}', "")


def _groom(gateway, monkeypatch, home):
    monkeypatch.setattr(sessions_groom.subprocess, "run", gateway)
    return sessions_groom.groom(openclaw="openclaw", now_ms=NOW, emit=lambda _line: None, home=home)


def test_an_archived_session_is_never_asked_and_the_run_does_not_fail(monkeypatch, tmp_path):
    _store(tmp_path, "capo", [(ARCHIVED, 1_700_000_000_000)])
    _store(tmp_path, "main", [(LIVE, None)])
    gateway = Gateway([_idle(ARCHIVED, 900_000), _idle(LIVE, 200_000)])

    report = _groom(gateway, monkeypatch, tmp_path)

    assert gateway.compactions == [LIVE], "la grande archiviata passava davanti e veniva rifiutata"
    assert report.failed == 0, "un giro fallito per un'archiviata è ciò che ha spento il job"


def test_without_the_filter_the_same_listing_is_the_outage(monkeypatch, tmp_path):
    """La prova che il test sopra riproduce il guasto e non un caso più comodo."""
    monkeypatch.setattr(sessions_groom, "drop_archived", lambda entries, state_dir: list(entries))
    gateway = Gateway([_idle(ARCHIVED, 900_000), _idle(LIVE, 200_000)])

    report = _groom(gateway, monkeypatch, tmp_path)

    assert gateway.compactions[0] == ARCHIVED
    assert report.failed == 1, "un fallimento basta all'exit code 1 che lo scheduler conta"


def test_the_archived_keys_come_from_every_agent_store(tmp_path):
    _store(tmp_path, "capo", [(ARCHIVED, 1), ("agent:capo:main", None)])
    _store(tmp_path, "main", [("agent:main:parity-2", 2), (LIVE, None)])

    found = sessions_archived.archived_keys(tmp_path / ".openclaw")

    assert found == {ARCHIVED, "agent:main:parity-2"}


def test_a_missing_store_or_state_keeps_the_listing_whole(tmp_path):
    rows = [_idle(LIVE, 1)]
    assert sessions_archived.drop_archived(rows, None) == rows
    assert sessions_archived.drop_archived(rows, tmp_path / "nessuna") == rows


def test_a_store_of_another_shape_keeps_the_listing_whole(tmp_path):
    """Una release che rinomina la colonna non nasconde niente: torna il comportamento di prima."""
    _store(tmp_path, "capo", [], schema="CREATE TABLE session_nodes (key TEXT)")
    rows = [_idle(ARCHIVED, 1)]
    assert sessions_archived.drop_archived(rows, tmp_path / ".openclaw") == rows


def test_the_store_is_opened_read_only(tmp_path):
    store = _store(tmp_path, "capo", [(ARCHIVED, 1)])
    before = store.read_bytes()
    sessions_archived.archived_keys(tmp_path / ".openclaw")
    assert store.read_bytes() == before
    assert not list(store.parent.glob("*-journal")) and not list(store.parent.glob("*-wal"))
