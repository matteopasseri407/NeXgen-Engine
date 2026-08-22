"""What a person reads follows their system; what the code says does not.

The rules that matter here are the failure modes. A missing translation must
degrade to English, never to a blank line or a raw key, and a translation that
has drifted out of step with its source must never take a command down — an
engine that crashes while trying to say something is worse than one that says
it in the wrong language.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import i18n


@pytest.fixture(autouse=True)
def _restore_detection():
    yield
    i18n.set_language(None)


def test_the_source_language_is_english():
    assert i18n.SOURCE_LANGUAGE == "en"


def test_an_untranslated_message_comes_out_in_english():
    i18n.set_language("it")
    # Nessun catalogo conterrà mai questa frase.
    assert i18n.t("A sentence nobody will ever translate") == "A sentence nobody will ever translate"


def test_a_language_with_no_catalog_falls_back_instead_of_failing():
    i18n.set_language("xx")
    assert i18n.t("Something") == "Something"


def test_fields_are_filled_in_after_translation(monkeypatch):
    # Tradurre e poi formattare è ciò che permette a chi traduce di spostare
    # i campi: l'ordine delle parole non è lo stesso in tutte le lingue.
    monkeypatch.setitem(i18n._CATALOGS, "it", {"{a} before {b}": "{b} prima di {a}"})
    i18n.set_language("it")
    assert i18n.t("{a} before {b}", a="uno", b="due") == "due prima di uno"


def test_a_translation_naming_a_field_that_vanished_does_not_crash(monkeypatch):
    monkeypatch.setitem(i18n._CATALOGS, "it", {"Hello {name}": "Ciao {nome_sbagliato}"})
    i18n.set_language("it")
    # Ripiega sulla sorgente, che è sempre in pari con il codice.
    assert i18n.t("Hello {name}", name="Matteo") == "Hello Matteo"


@pytest.mark.parametrize(
    ("value", "expected"),
    [("it_IT.UTF-8", "it"), ("it", "it"), ("en_GB", "en"), ("C", "en"), ("POSIX", "en")],
)
def test_the_system_locale_decides(monkeypatch, value: str, expected: str):
    for name in ("NEXGEN_LANG", "LC_ALL", "LC_MESSAGES"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LANG", value)
    i18n.set_language(None)
    assert i18n.detect_language() == expected


def test_an_explicit_choice_beats_the_system(monkeypatch):
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    monkeypatch.setenv("NEXGEN_LANG", "it")
    i18n.set_language(None)
    assert i18n.detect_language() == "it"


def test_italian_ships_and_english_is_always_listed():
    languages = i18n.available_languages()
    assert "en" in languages
    assert "it" in languages


def test_every_catalog_entry_is_a_string_pair():
    """Un catalogo malformato deve rompere qui, non davanti all'utente."""
    for language in i18n.available_languages():
        if language == i18n.SOURCE_LANGUAGE:
            continue
        catalog = i18n._load_catalog(language)
        for source, translation in catalog.items():
            assert isinstance(source, str) and source, f"{language}: chiave non valida"
            assert isinstance(translation, str) and translation, f"{language}: '{source}' senza traduzione"


def test_no_translation_invents_a_field_the_source_lacks():
    """I campi di una traduzione devono esistere nella sorgente.

    Se una traduzione nomina `{porta}` e la sorgente ha `{port}`, il messaggio
    ripiega silenziosamente sull'inglese per sempre. Meglio accorgersene qui.
    """
    import re

    field = re.compile(r"\{(\w+)\}")
    for language in i18n.available_languages():
        if language == i18n.SOURCE_LANGUAGE:
            continue
        for source, translation in i18n._load_catalog(language).items():
            extra = set(field.findall(translation)) - set(field.findall(source))
            assert not extra, f"{language}: '{source}' traduce con campi inesistenti: {extra}"
