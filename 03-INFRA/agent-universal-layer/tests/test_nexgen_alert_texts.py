"""Ogni avviso che il megafono può mandare, misurato e riletto in due lingue.

Un avviso non lo legge chi ha scritto il controllo: lo legge una persona che ha
installato il motore per tenere in ordine le proprie note. Due guasti capitano in
silenzio: il gergo, che dice "sync" e "guardia" a chi non sa cosa siano, e la
lunghezza, perché un toast tronca il corpo e la coda è dove sta la cosa da fare.
Questi test camminano su tutti gli avvisi del catalogo, in inglese e in italiano,
e rifiutano entrambi.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import alerts, i18n  # noqa: E402 - cloned checkout, sys.path first

LANGUAGES = ("en", "it")

#: Parole che dentro un avviso non vogliono dire niente per chi lo legge: o sono
#: il nome di un componente del motore, o sono il mestiere di chi lo scrive.
BANNED = (
    "guard",
    "beat",
    "sidecar",
    "megaphone",
    "megafono",
    "heartbeat",
    "liveness",
    "webhook",
    "aumid",
    "appusermodelid",
    "payload",
    "daemon",
    "nexgen_core",
    ".py",
)


@pytest.fixture(autouse=True)
def _restore_language():
    yield
    i18n.set_language(None)


@pytest.fixture(params=LANGUAGES)
def language(request, monkeypatch):
    """Ogni test gira due volte: una per la lingua che una persona legge."""
    i18n.set_language(request.param)
    return request.param


def _alerts() -> list[tuple[str, alerts.Alert]]:
    return sorted(alerts.catalog().items())


def test_the_catalog_is_the_engine_s_own_and_is_not_empty():
    names = [name for name, _ in _alerts()]
    assert "SYNC_STALLED" in names
    assert "QUOTA_AT" in names
    assert len(names) >= 12


@pytest.mark.parametrize("name,alert", _alerts(), ids=[name for name, _ in _alerts()])
def test_every_alert_fits_a_toast_in_both_languages(name, alert, language):
    title, message, _action = alert.say(**alert.sample)
    assert len(title) <= alerts.TITLE_LIMIT, f"{language}/{name}: titolo di {len(title)} caratteri"
    assert len(message) <= alerts.BODY_LIMIT, f"{language}/{name}: corpo di {len(message)} caratteri"


@pytest.mark.parametrize("name,alert", _alerts(), ids=[name for name, _ in _alerts()])
def test_no_placeholder_is_left_unfilled(name, alert, language):
    """Un campo senza valore arriverebbe sullo schermo con le graffe dentro."""
    title, message, action = alert.say(**alert.sample)
    for part in (title, message, action):
        assert "{" not in part and "}" not in part, f"{language}/{name}: {part}"


@pytest.mark.parametrize("name,alert", _alerts(), ids=[name for name, _ in _alerts()])
def test_every_alert_names_no_internal_component(name, alert, language):
    title, message, action = alert.say(**alert.sample)
    for word in BANNED:
        for part in (title, message, action):
            assert word not in part.lower(), f"{language}/{name}: '{word}' in '{part}'"


@pytest.mark.parametrize("name,alert", _alerts(), ids=[name for name, _ in _alerts()])
def test_every_sentence_ends_properly(name, alert, language):
    _title, message, action = alert.say(**alert.sample)
    assert message.endswith("."), f"{language}/{name}: {message}"
    if action:
        assert action.endswith(".") or not action.endswith(" "), f"{language}/{name}: {action}"


@pytest.mark.parametrize("name,alert", _alerts(), ids=[name for name, _ in _alerts()])
def test_the_action_is_one_pasteable_command(name, alert, language):
    """Una sola azione, pronta da incollare: niente due comandi e niente prosa."""
    _title, _message, action = alert.say(**alert.sample)
    if not action:
        return
    assert "\n" not in action, f"{language}/{name}: {action}"
    assert ";" not in action and " e poi " not in action, f"{language}/{name}: {action}"
    assert len(action) <= alerts.TITLE_LIMIT, f"{language}/{name}: {action}"


@pytest.mark.parametrize("name,alert", _alerts(), ids=[name for name, _ in _alerts()])
def test_every_alert_is_translated_into_italian(name, alert):
    """Una traduzione mancante ripiega sull'inglese: qui si vede subito."""
    i18n.set_language("en")
    english_title, english_message, _ = alert.say(**alert.sample)
    i18n.set_language("it")
    italian_title, italian_message, _ = alert.say(**alert.sample)
    assert italian_title != english_title, f"{name}: titolo non tradotto"
    assert italian_message != english_message, f"{name}: corpo non tradotto"


def test_the_fields_a_translation_uses_are_the_ones_the_source_has():
    """Un campo inventato dalla traduzione farebbe ripiegare l'avviso sull'inglese."""
    field = re.compile(r"\{(\w+)\}")
    catalog = i18n._load_catalog("it")
    for name, alert in _alerts():
        for part in (alert.title, alert.message, alert.action):
            if not part:
                continue
            italian = catalog.get(part, "")
            assert not (set(field.findall(italian)) - set(field.findall(part))), name


def test_every_alert_says_the_one_thing_to_do_about_it():
    """Un avviso senza azione deve dirlo: o c'è un comando, o il motore ha già agito."""
    no_action = {name for name, alert in _alerts() if not alert.action}
    # Questi raccontano qualcosa che il motore ha già fatto da solo: niente da digitare.
    assert no_action == {
        "MODEL_FAILED", "MODEL_OUT_OF_QUOTA", "WORKER_RELAUNCHED", "WORKER_STUCK",
    }, no_action


def test_the_council_line_is_plain_and_fits_too(monkeypatch):
    """Anche il consiglio parla alla persona: le sue frasi fisse passano le stesse regole."""
    # I moduli del consiglio si importano per nome, con la loro cartella in testa al
    # percorso: è lo stesso schema con cui li carica `council.py`.
    council_dir = Path(__file__).resolve().parents[1] / "council"
    if str(council_dir) not in sys.path:
        sys.path.insert(0, str(council_dir))
    import announce

    for language in LANGUAGES:
        i18n.set_language(language)
        title, message, action = announce.outcome_lines(
            "consult", "c1", "APPROVE=2", "/kept/council-q", "",
        )
        assert len(title) <= alerts.TITLE_LIMIT
        assert len(message) <= alerts.BODY_LIMIT
        for word in BANNED:
            assert word not in f"{title} {message} {action}".lower()
