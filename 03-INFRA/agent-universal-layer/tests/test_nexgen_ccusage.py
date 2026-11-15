"""Il guscio attorno a `ccusage`: un giro solo, un limite di tempo, e una risposta capita.

Nessun test avvia il binario vero: `spawn` è finto, e i casi che contano sono
quelli che una macchina produce davvero (binario assente, uscita non zero, shim
npm che non parte, risposta che non è JSON). La forma del documento è quella
registrata in `fixtures/ccusage-opencode-session.json`, presa da `ccusage` 20.0.51.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from nexgen_core import ccusage

#: Un documento valido in miniatura: le due chiavi che ogni vista stampa insieme.
DOCUMENT = json.dumps({"sessions": [], "totals": {"totalCost": 0.0}})


def _binary(monkeypatch, path: str | None = "/usr/bin/ccusage") -> None:
    """Un `PATH` che trova o non trova `ccusage`."""
    monkeypatch.setattr(ccusage, "binary", lambda: path)


def _spawn(monkeypatch, *, code: int = 0, out: str = DOCUMENT) -> list[tuple[list[str], Path]]:
    """Un eseguibile che risponde, e le chiamate che ha ricevuto."""
    runs: list[tuple[list[str], Path]] = []

    def spawn(argv: list[str], cwd: Path) -> tuple[int, str]:
        runs.append((argv, cwd))
        return code, out

    monkeypatch.setattr(ccusage, "spawn", spawn)
    return runs


def test_a_run_carries_the_date_filters_the_binary_documents(tmp_path, monkeypatch):
    """`--since` e `--until` sono le due opzioni di `ccusage opencode session --help`."""
    _binary(monkeypatch)
    runs = _spawn(monkeypatch)

    answer = ccusage.run(("opencode", "session", "--json"), cwd=tmp_path,
                         since="2026-11-01", until="2026-11-15")

    assert answer == ccusage.Answer(out=DOCUMENT)
    assert runs == [(
        ["/usr/bin/ccusage", "opencode", "session", "--json",
         "--since", "2026-11-01", "--until", "2026-11-15"],
        tmp_path,
    )]


def test_no_filter_is_no_argument(tmp_path, monkeypatch):
    """Senza filtro la riga di comando resta quella documentata, senza opzioni vuote."""
    _binary(monkeypatch)
    runs = _spawn(monkeypatch)

    ccusage.run(("opencode", "session", "--json"), cwd=tmp_path)

    assert runs[0][0] == ["/usr/bin/ccusage", "opencode", "session", "--json"]


def test_a_missing_binary_is_an_answer_never_an_exception(tmp_path, monkeypatch):
    """`ccusage` è opzionale: la sua assenza è una parola, e nessun processo parte."""
    _binary(monkeypatch, None)
    runs = _spawn(monkeypatch)

    assert ccusage.run(("opencode", "session", "--json"), cwd=tmp_path) == ccusage.Answer(error=ccusage.MISSING)
    assert runs == []


def test_a_non_zero_exit_names_the_code(tmp_path, monkeypatch):
    """Un'uscita non zero non è una risposta: il codice è ciò che il chiamante stampa."""
    _binary(monkeypatch)
    _spawn(monkeypatch, code=3)

    assert ccusage.run(("opencode", "session", "--json"), cwd=tmp_path) == ccusage.Answer(
        error=ccusage.FAILED, detail="3")


def test_a_timeout_names_the_deadline(tmp_path, monkeypatch):
    """Un giro che supera il limite viene fermato e detto, mai atteso per sempre."""
    _binary(monkeypatch)

    def spawn(argv: list[str], cwd: Path) -> tuple[int, str]:
        raise subprocess.TimeoutExpired(cmd=argv, timeout=ccusage.RUN_TIMEOUT_SECONDS)

    monkeypatch.setattr(ccusage, "spawn", spawn)

    assert ccusage.run(("opencode", "session", "--json"), cwd=tmp_path) == ccusage.Answer(
        error=ccusage.TIMEOUT, detail="30")


def test_a_shim_that_cannot_start_is_the_missing_one(tmp_path, monkeypatch):
    """Uno shim npm il cui ingresso `node` è sparito non è un guasto: è il binario che manca."""
    _binary(monkeypatch)

    def spawn(argv: list[str], cwd: Path) -> tuple[int, str]:
        raise OSError("no such file")

    monkeypatch.setattr(ccusage, "spawn", spawn)

    assert ccusage.run(("opencode", "session", "--json"), cwd=tmp_path) == ccusage.Answer(error=ccusage.MISSING)


def test_a_document_without_totals_is_refused_whole():
    """Le due chiavi escono insieme: una sola è un documento che si è mosso."""
    with pytest.raises(ccusage.FormatError) as refused:
        ccusage.document(json.dumps({"sessions": []}), "sessions")

    assert str(refused.value) == "there is no totals object"


def test_a_document_without_the_rows_is_refused_whole():
    """La vista chiesta è la sola che questo lettore sa leggere."""
    with pytest.raises(ccusage.FormatError) as refused:
        ccusage.document(DOCUMENT, "daily")

    assert str(refused.value) == "there is no daily list"


def test_text_that_is_not_json_is_refused():
    """Un avviso stampato al posto del documento non è un numero da mostrare."""
    with pytest.raises(ccusage.FormatError):
        ccusage.document("<html>manutenzione</html>", "sessions")


def test_a_row_that_is_not_an_object_is_refused():
    """Una riga che non è un oggetto non ha campi: si rifiuta, non si indovina."""
    with pytest.raises(ccusage.FormatError) as refused:
        ccusage.object_row("ses_uno", "riga 1", "sessions")

    assert str(refused.value) == "sessions: riga 1 is not an object"


def test_a_boolean_is_not_a_count():
    """`true` non è un numero di token: un conteggio booleano sarebbe una bugia arrotondata."""
    with pytest.raises(ccusage.FormatError):
        ccusage.require_count({"totalTokens": True}, "totalTokens", "sessione ses_uno")


def test_the_store_gives_the_same_answer_back(tmp_path):
    """La cache è un giro di andata e ritorno, e un file che non c'è è un semplice vuoto."""
    path = tmp_path / "stato" / "ccusage.json"
    path.parent.mkdir()

    assert ccusage.cache_read(path) is None
    ccusage.cache_write(path, {"read_at": 100.0, "sessions": []})

    assert ccusage.cache_read(path) == {"read_at": 100.0, "sessions": []}
    assert ccusage.cache_is_fresh(ccusage.cache_read(path), moment=100.0 + 599) is True
    assert ccusage.cache_is_fresh(ccusage.cache_read(path), moment=100.0 + 601) is False


def test_an_answer_from_the_future_is_stale():
    """Un orologio che torna indietro deve comprare una lettura, non fidarsi della vecchia."""
    assert ccusage.cache_is_fresh({"read_at": 200.0}, moment=100.0) is False
    assert ccusage.cache_is_fresh({"read_at": "ieri"}, moment=100.0) is False


def _config(tmp_path, body: str) -> Path:
    """Un `quota.yaml` scritto per il test."""
    path = tmp_path / "quota.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_the_binary_is_asked_unless_the_file_says_off(tmp_path):
    """La sezione è opzionale, e una riga `auto` è la stessa cosa che non scriverla."""
    assert ccusage.enabled(None) is True
    assert ccusage.enabled(tmp_path / "non-esiste.yaml") is True
    assert ccusage.enabled(_config(tmp_path, "backends:\n  ccusage: auto\n")) is True
    assert ccusage.enabled(_config(tmp_path, "providers:\n  opencode-go:\n    5h: 5\n")) is True


def test_off_is_off_in_both_spellings(tmp_path):
    """YAML legge un `off` nudo come il booleano falso: valgono entrambe le scritture."""
    assert ccusage.enabled(_config(tmp_path, "backends:\n  ccusage: off\n")) is False
    assert ccusage.enabled(_config(tmp_path, "backends:\n  ccusage: OFF\n")) is False


def test_a_file_that_cannot_be_read_turns_nothing_off(tmp_path):
    """Un `quota.yaml` rotto è un altro reclamo: non spegne in silenzio un backend."""
    assert ccusage.enabled(_config(tmp_path, "backends: [non chiuso\n")) is True
