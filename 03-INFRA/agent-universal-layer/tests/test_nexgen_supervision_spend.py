"""Le tre finestre di spesa lette dall'archivio del harness: database finto, niente `opencode`.

Ogni test costruisce un `opencode.db` in miniatura con la sola tabella che serve,
e fissa l'istante di lettura: le finestre sono aritmetica, non fortuna.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from nexgen_local import supervision_spend as spend

#: Un istante fisso: le soglie delle finestre si calcolano a mano nel test.
NOW = 1_800_000_000.0


def _archive(tmp_path, rows, *, table: str = "session_v2", also: str = "") -> Path:
    """Un archivio in miniatura: righe `(id, parent_id, costo, secondi fa)`.

    `also` crea una seconda tabella delle sessioni con le stesse righe, per
    provare che una sola viene sommata.
    """
    path = tmp_path / "opencode.db"
    con = sqlite3.connect(path)
    for name in (table, also):
        if not name:
            continue
        con.execute(
            f"create table if not exists {name} "
            "(id text primary key, parent_id text, cost real, time_updated integer)"
        )
        con.executemany(
            f"insert into {name} values (?, ?, ?, ?)",
            [
                (session, parent, cost, int((NOW - ago) * 1000))
                for session, parent, cost, ago in rows
            ],
        )
    con.commit()
    con.close()
    return path


def _costs(data: dict) -> dict:
    return {key: window["cost_usd"] for key, window in data["windows"].items()}


def _sessions(data: dict) -> dict:
    return {key: window["sessions"] for key, window in data["windows"].items()}


def test_the_three_windows_sum_what_the_archive_says(tmp_path):
    db = _archive(tmp_path, [
        ("ses_recente", None, 0.5, 3600),          # un'ora fa
        ("ses_media", None, 2.0, 3 * 86400),       # tre giorni fa
        ("ses_vecchia", None, 4.0, 20 * 86400),    # venti giorni fa
        ("ses_fuori", None, 8.0, 31 * 86400),      # oltre i trenta giorni
    ])

    data = spend.spend_windows(db=db, now=NOW)

    assert data["note"] == ""
    assert "session_v2" in data["source"]
    assert _costs(data) == {"5h": 0.5, "7d": 2.5, "30d": 6.5}
    assert _sessions(data) == {"5h": 1, "7d": 2, "30d": 3}
    assert data["read_at"].endswith("+00:00")


def test_a_child_counts_in_the_window_of_its_own_last_update(tmp_path):
    """Le figlie sono righe come le altre: il costo del subagente non va perso."""
    db = _archive(tmp_path, [
        ("ses_padre", None, 1.0, 6 * 3600),        # fuori dalle 5 ore
        ("ses_figlia", "ses_padre", 0.25, 2 * 3600),
    ])

    data = spend.spend_windows(db=db, now=NOW)

    assert _costs(data) == {"5h": 0.25, "7d": 1.25, "30d": 1.25}
    assert _sessions(data)["5h"] == 1


def test_the_live_table_is_preferred_so_a_session_is_counted_once(tmp_path):
    """`session` ripete le righe di `session_v2`: sommare entrambe raddoppierebbe la spesa."""
    db = _archive(
        tmp_path,
        [("ses_uno", None, 3.0, 3600)],
        table="session_v2", also="session",
    )

    data = spend.spend_windows(db=db, now=NOW)

    assert _costs(data)["5h"] == 3.0
    assert "session_v2" in data["source"]


def test_the_old_table_is_read_when_the_live_one_is_missing(tmp_path):
    db = _archive(tmp_path, [("ses_uno", None, 1.5, 3600)], table="session")

    data = spend.spend_windows(db=db, now=NOW)

    assert _costs(data)["5h"] == 1.5
    assert "session)" in data["source"]


def test_a_missing_store_is_unknown_never_zero(tmp_path):
    data = spend.spend_windows(db=tmp_path / "manca.db", now=NOW)

    assert data["note"] == "archivio del harness non trovato"
    assert _costs(data) == {"5h": None, "7d": None, "30d": None}
    assert _sessions(data) == {"5h": None, "7d": None, "30d": None}


def test_a_store_that_is_not_a_database_says_so(tmp_path):
    path = tmp_path / "rotto.db"
    path.write_text("non sono un database\n", encoding="utf-8")

    data = spend.spend_windows(db=path, now=NOW)

    assert "non leggibile" in data["note"]
    assert _costs(data)["5h"] is None


def test_a_store_without_a_session_table_says_so(tmp_path):
    path = tmp_path / "vuoto.db"
    con = sqlite3.connect(path)
    con.execute("create table altra_cosa (id text)")
    con.commit()
    con.close()

    data = spend.spend_windows(db=path, now=NOW)

    assert data["note"] == "archivio senza la tabella delle sessioni"
    assert _costs(data)["30d"] is None


def test_a_huge_spend_is_reported_and_nothing_refuses_it(tmp_path):
    """Il motore mostra, non decide: nessuna soglia, nessun rifiuto."""
    db = _archive(tmp_path, [("ses_costosa", None, 50_000.0, 60)])

    data = spend.spend_windows(db=db, now=NOW)

    assert data["note"] == ""
    assert _costs(data)["5h"] == 50_000.0


def test_an_empty_archive_reports_zero_for_a_window_nobody_spent_in(tmp_path):
    """Una lettura riuscita che non trova sessioni è zero misurato, non un numero mancante."""
    db = _archive(tmp_path, [("ses_vecchia", None, 1.0, 40 * 86400)])

    data = spend.spend_windows(db=db, now=NOW)

    assert _costs(data) == {"5h": 0.0, "7d": 0.0, "30d": 0.0}
    assert _sessions(data) == {"5h": 0, "7d": 0, "30d": 0}
