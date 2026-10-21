"""L'ultimo turno di una sessione: riuscito, o chiuso dal provider con un errore.

Un archivio SQLite finto con la sola tabella che serve: nessun OpenCode, nessuna
sessione vera. Quello che si prova è la lettura del messaggio `idle` e il tipo di
errore che ne esce, compreso il caso in cui l'archivio non si può leggere.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from nexgen_local import supervision_turns as turns


def _store(tmp_path, rows) -> object:
    """One session's messages, in the table the harness itself writes."""
    path = tmp_path / "opencode.db"
    con = sqlite3.connect(path)
    con.execute(
        "create table session_message (id text, session_id text, type text, seq int,"
        " time_created int, time_updated int, data text)"
    )
    for index, (kind, data) in enumerate(rows):
        con.execute(
            "insert into session_message values (?, ?, ?, ?, ?, ?, ?)",
            (f"m{index}", "ses_prova", kind, index, 1000 + index, 1000 + index, json.dumps(data)),
        )
    con.commit()
    con.close()
    return path


def _failed(at_ms: int = 0) -> dict:
    return {
        "outcome": "failed",
        "error": {"type": "provider.content-filter", "message": "Provider blocked the response"},
        "time": {"created": at_ms},
    }


def test_a_turn_the_provider_cut_off_names_its_error_type(tmp_path):
    db = _store(tmp_path, [("user", {"text": "continua"}), ("idle", _failed(2000))])

    turn = turns.last_turn("ses_prova", db=db)

    assert turn.failed is True
    assert turn.error_type == "provider.content-filter"
    assert turn.why() == "provider.content-filter"
    assert turn.at_ms == 1001, "l'istante è quello del messaggio, come lo scrive l'archivio"


def test_a_turn_that_finished_is_not_a_failure(tmp_path):
    db = _store(tmp_path, [("idle", {"outcome": "succeeded"})])

    turn = turns.last_turn("ses_prova", db=db)

    assert turn.failed is False
    assert turn.why() == "succeeded"


def test_the_last_idle_message_is_the_one_that_counts(tmp_path):
    """Un errore vecchio non deve coprire il turno che è andato a buon fine dopo."""
    db = _store(tmp_path, [("idle", _failed()), ("idle", {"outcome": "succeeded"})])

    assert turns.last_turn("ses_prova", db=db).failed is False


def test_a_session_with_no_turn_yet_has_no_outcome(tmp_path):
    db = _store(tmp_path, [("user", {"text": "ciao"})])

    turn = turns.last_turn("ses_prova", db=db)

    assert turn == turns.Turn()
    assert not turn.failed


def test_a_message_that_does_not_parse_is_not_a_failure(tmp_path):
    path = tmp_path / "rotto.db"
    con = sqlite3.connect(path)
    con.execute(
        "create table session_message (id text, session_id text, type text, seq int,"
        " time_created int, time_updated int, data text)"
    )
    con.execute(
        "insert into session_message values ('m0', 'ses_prova', 'idle', 0, 5000, 5000, ?)",
        ("{non-json",),
    )
    con.commit()
    con.close()

    turn = turns.last_turn("ses_prova", db=path)

    assert not turn.failed
    assert turn.at_ms == 5000


def test_a_store_that_is_not_there_is_an_error_the_caller_can_read(tmp_path, monkeypatch):
    monkeypatch.setattr(turns.costs, "opencode_db", lambda: None)

    with pytest.raises(turns.TurnError, match="non trovato"):
        turns.last_turn("ses_prova")


def test_a_store_without_the_messages_table_is_an_error_not_a_success(tmp_path):
    path = tmp_path / "vuoto.db"
    sqlite3.connect(path).close()

    with pytest.raises(turns.TurnError, match="non leggibile"):
        turns.last_turn("ses_prova", db=path)
