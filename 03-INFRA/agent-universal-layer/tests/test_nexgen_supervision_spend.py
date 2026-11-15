"""Le tre finestre di spesa lette dall'archivio del harness: database finto, niente `opencode`.

Ogni test costruisce un `opencode.db` in miniatura con la sola tabella che serve,
e fissa l'istante di lettura: le finestre sono aritmetica, non fortuna.

Il prezzo per sessione lo dà `ccusage` quando c'è, e i test lo fingono: nessuno
avvia il binario vero. Il ripiego è la colonna `cost` dell'archivio, che è il
numero che questi test si aspettavano prima che il binario esistesse.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from nexgen_core import ccusage, quota
from nexgen_local import supervision_spend as spend

#: Un istante fisso: le soglie delle finestre si calcolano a mano nel test.
NOW = 1_800_000_000.0

#: La risposta vera di `ccusage` 20.0.51 (MIT) a `opencode session --json` su un
#: archivio in miniatura con una sessione e la sua fork: la fork copiata vale 0.5,
#: il genitore 7.0, e il totale dice 7.5.
PRICES_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "ccusage-opencode-session.json"


@pytest.fixture(autouse=True)
def _no_real_binary(monkeypatch, tmp_path):
    """Nessun test avvia il binario vero, e la lista dei prezzi resta dentro il test.

    `Answer(error=MISSING)` è la macchina senza `ccusage`: il ripiego di oggi è
    quello che ogni test già si aspetta. La cache va in `tmp_path`, così una
    lettura di prova non tocca lo stato della macchina che esegue i test, e
    `quota.yaml` è un file che non esiste: l'interruttore è quello di serie.
    """
    monkeypatch.setattr(ccusage, "run", lambda *args, **kwargs: ccusage.Answer(error=ccusage.MISSING))
    monkeypatch.setattr(spend, "prices_cache_path", lambda home=None: tmp_path / "ccusage-opencode.json")
    monkeypatch.setattr(quota, "limits_path", lambda home=None, **kwargs: tmp_path / "quota.yaml")


def _answers(monkeypatch, text: str = "", *, error: str = "") -> list[tuple[tuple[str, ...], Path]]:
    """Un `ccusage` che risponde `text` (o `error`), e le chiamate che ha ricevuto."""
    runs: list[tuple[tuple[str, ...], Path]] = []

    def run(command, *, cwd, since=None, until=None):
        runs.append((tuple(command), Path(cwd)))
        return ccusage.Answer(out=text, error=error)

    monkeypatch.setattr(ccusage, "run", run)
    return runs


def _recorded() -> str:
    """La risposta registrata, così com'è."""
    return PRICES_FIXTURE.read_text(encoding="utf-8")


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


# --------------------------------------------------- la stessa spesa, per fornitore


def _archive_with_models(tmp_path, rows, *, model_column: bool = True) -> Path:
    """Un archivio in miniatura con la colonna `model`, righe `(id, costo, secondi fa, modello)`."""
    path = tmp_path / "opencode.db"
    con = sqlite3.connect(path)
    columns = "id text primary key, cost real, time_updated integer"
    if model_column:
        columns += ", model text"
    con.execute(f"create table session_v2 ({columns})")
    for session, cost, ago, model in rows:
        values = (session, cost, int((NOW - ago) * 1000), model) if model_column else (session, cost, int((NOW - ago) * 1000))
        placeholders = "?, ?, ?, ?" if model_column else "?, ?, ?"
        con.execute(f"insert into session_v2 values ({placeholders})", values)
    con.commit()
    con.close()
    return path


def _model(provider: str) -> str:
    return '{"id":"deepseek-v4.1-flash","providerID":"%s","variant":"max"}' % provider


def test_the_flat_subscription_and_zen_are_two_separate_invoices(tmp_path):
    """`opencode-go` e `opencode` sono due abbonamenti diversi: sommarli risponde alla domanda sbagliata."""
    db = _archive_with_models(tmp_path, [
        ("ses_go", 1.5, 3600, _model("opencode-go")),
        ("ses_zen", 0.25, 3600, _model("opencode")),
        ("ses_go_vecchia", 4.0, 10 * 86400, _model("opencode-go")),
    ])

    data = spend.spend_by_provider(db=db, now=NOW)

    assert data["note"] == ""
    assert "session_v2" in data["source"]
    assert data["providers"]["opencode-go"]["5h"]["cost_usd"] == 1.5
    assert data["providers"]["opencode-go"]["30d"]["cost_usd"] == 5.5
    assert data["providers"]["opencode"]["5h"]["cost_usd"] == 0.25
    assert data["providers"]["opencode"]["30d"]["cost_usd"] == 0.25


def test_a_free_model_costs_zero_and_stays_in_its_own_provider(tmp_path):
    """I modelli `-free` costano zero: il loro fornitore resta leggibile, il totale no."""
    db = _archive_with_models(tmp_path, [
        ("ses_free", 0.0, 600, '{"id":"deepseek-v4.1-flash-free","providerID":"opencode"}'),
    ])

    data = spend.spend_by_provider(db=db, now=NOW)

    assert data["providers"]["opencode"]["5h"] == {"cost_usd": 0.0, "sessions": 1}


def test_a_session_without_a_provider_is_counted_not_dropped(tmp_path):
    db = _archive_with_models(tmp_path, [
        ("ses_muta", 2.0, 3600, None),
        ("ses_rotta", 1.0, 3600, "non-json"),
    ])

    data = spend.spend_by_provider(db=db, now=NOW)

    assert data["providers"][spend.UNKNOWN_PROVIDER]["5h"] == {"cost_usd": 3.0, "sessions": 2}


def test_an_archive_from_before_the_model_column_still_reads(tmp_path):
    """La colonna `model` è arrivata con la v2: un archivio vecchio non deve far fallire la lettura."""
    db = _archive_with_models(tmp_path, [("ses_una", 1.25, 3600, None)], model_column=False)

    data = spend.spend_by_provider(db=db, now=NOW)

    assert data["note"] == ""
    assert data["providers"][spend.UNKNOWN_PROVIDER]["5h"]["cost_usd"] == 1.25


def test_an_unreadable_store_has_no_providers_at_all(tmp_path):
    """Un archivio che non si legge non è zero speso: non ha fornitori inventati."""
    data = spend.spend_by_provider(db=tmp_path / "manca.db", now=NOW)

    assert data["note"] == "archivio del harness non trovato"
    assert data["providers"] == {}


# ------------------------------------- il prezzo per sessione lo dà ccusage, quando c'è


def test_the_recorded_answer_is_one_price_per_session():
    """La risposta registrata di ccusage: la fork copiata vale solo ciò che ha scritto lei."""
    assert spend.prices_from(_recorded()) == {"fork": 0.5, "parent": 7.0}


def test_ccusage_prices_the_sessions_the_archive_recorded_for_free(tmp_path, monkeypatch):
    """L'abbonamento piatto scrive `cost` 0: la colonna da sola direbbe "niente speso".

    Senza ccusage questa archivio vale 7.2 + 3.5 = 10.7, perché la fork porta nel
    proprio costo anche la storia copiata; con ccusage vale 7.0 + 0.5 = 7.5.
    """
    db = _archive(tmp_path, [
        ("parent", None, 7.2, 3600),
        ("fork", "parent", 3.5, 3600),
    ])
    _answers(monkeypatch, _recorded())

    data = spend.spend_windows(db=db, now=NOW)

    assert data["note"] == ""
    assert data["source"] == "opencode.db (session_v2) + ccusage"
    assert _costs(data) == {"5h": 7.5, "7d": 7.5, "30d": 7.5}
    assert _sessions(data) == {"5h": 2, "7d": 2, "30d": 2}


def test_a_session_ccusage_does_not_name_keeps_the_archives_own_cost(tmp_path, monkeypatch):
    """Una sessione che ccusage non nomina non sparisce e non eredita un prezzo altrui."""
    db = _archive(tmp_path, [
        ("parent", None, 7.2, 3600),
        ("ses_muta", None, 2.0, 3600),
    ])
    _answers(monkeypatch, _recorded())

    data = spend.spend_windows(db=db, now=NOW)

    assert _costs(data)["5h"] == 9.0


def test_a_price_for_a_session_the_archive_does_not_hold_is_ignored(tmp_path, monkeypatch):
    """La risposta è per id: un id che l'archivio non ha (un canale, un backup) non entra."""
    db = _archive(tmp_path, [("parent", None, 7.2, 3600)])
    _answers(monkeypatch, _recorded())

    data = spend.spend_windows(db=db, now=NOW)

    assert _costs(data)["5h"] == 7.0


def test_without_the_binary_the_sum_is_the_one_from_before(tmp_path):
    """Senza ccusage installato il numero è la colonna `cost`, e la fonte lo dice."""
    db = _archive(tmp_path, [("ses_uno", None, 3.0, 3600)])

    data = spend.spend_windows(db=db, now=NOW)

    assert data["source"] == "opencode.db (session_v2)"
    assert _costs(data)["5h"] == 3.0


def test_the_switch_in_quota_yaml_silences_the_reader(tmp_path, monkeypatch):
    """Un binario, un interruttore: `ccusage: off` vale anche per l'archivio dell'harness."""
    db = _archive(tmp_path, [("parent", None, 7.2, 3600)])
    runs = _answers(monkeypatch, _recorded())
    config = tmp_path / "quota.yaml"
    config.write_text("backends:\n  ccusage: off\n", encoding="utf-8")
    monkeypatch.setattr(quota, "limits_path", lambda home=None, **kwargs: config)

    data = spend.spend_windows(db=db, now=NOW)

    assert runs == []
    assert _costs(data)["5h"] == 7.2


def test_a_broken_answer_leaves_todays_sum_and_is_asked_once(tmp_path, monkeypatch):
    """Una risposta che non si capisce costa il prezzo, non il numero: e non si richiede."""
    db = _archive(tmp_path, [("ses_uno", None, 3.0, 3600)])
    runs = _answers(monkeypatch, "<html>non è json</html>")

    first = spend.spend_windows(db=db, now=NOW)
    second = spend.spend_windows(db=db, now=NOW + 60)

    assert _costs(first)["5h"] == 3.0
    assert _costs(second)["5h"] == 3.0
    assert first["source"] == "opencode.db (session_v2)"
    assert len(runs) == 1


def test_the_price_list_is_reused_for_ten_minutes(tmp_path, monkeypatch):
    """Un giro su un archivio grande costa secondi: la stessa risposta basta dieci minuti."""
    db = _archive(tmp_path, [("parent", None, 7.2, 3600)])
    runs = _answers(monkeypatch, _recorded())

    spend.spend_windows(db=db, now=NOW)
    spend.spend_windows(db=db, now=NOW + 599)
    assert len(runs) == 1

    spend.spend_windows(db=db, now=NOW + 601)
    assert len(runs) == 2


def test_a_price_list_bought_for_another_archive_is_not_used(tmp_path, monkeypatch):
    """La cache è legata all'archivio che l'ha comprata: prezzi di un altro db sarebbero sbagliati."""
    first = _archive(tmp_path, [("parent", None, 7.2, 3600)])
    second = tmp_path / "altro" / "opencode.db"
    second.parent.mkdir()
    second.write_bytes(first.read_bytes())
    runs = _answers(monkeypatch, _recorded())

    spend.spend_windows(db=first, now=NOW)
    spend.spend_windows(db=second, now=NOW + 60)

    assert len(runs) == 2


def test_the_question_asks_for_the_offline_view_in_the_archives_folder(tmp_path, monkeypatch):
    """La vista letta, e nessun accesso alla rete: `--offline` è il listino incluso nel binario."""
    db = _archive(tmp_path, [("parent", None, 7.2, 3600)])
    runs = _answers(monkeypatch, _recorded())

    spend.spend_windows(db=db, now=NOW)

    assert runs == [(("opencode", "session", "--json", "--offline", "--no-color"), tmp_path)]


def test_a_provider_split_uses_the_same_prices(tmp_path, monkeypatch):
    """Il fornitore lo dice l'archivio, il prezzo lo dice ccusage: le due metà restano separate."""
    db = _archive_with_models(tmp_path, [
        ("parent", 0.0, 3600, _model("opencode-go")),
        ("fork", 3.5, 3600, _model("opencode-go")),
    ])
    _answers(monkeypatch, _recorded())

    data = spend.spend_by_provider(db=db, now=NOW)

    assert data["providers"]["opencode-go"]["5h"] == {"cost_usd": 7.5, "sessions": 2}
    assert "+ ccusage" in data["source"]


def test_a_row_without_a_total_cost_is_refused_whole():
    """Una riga a metà non è un prezzo: la lista si rifiuta intera e vale il ripiego."""
    document = json.dumps({"sessions": [{"sessionId": "ses_uno"}], "totals": {}})

    with pytest.raises(ccusage.FormatError) as refused:
        spend.prices_from(document)

    assert "sessione ses_uno" in str(refused.value)
