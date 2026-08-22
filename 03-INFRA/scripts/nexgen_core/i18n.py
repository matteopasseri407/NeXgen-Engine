#!/usr/bin/env python3
"""What the engine says to a person, in the language that person reads.

The code is English — identifiers, docstrings, comments, and the message
strings themselves — because it is read by whoever maintains it and by the
assistants that work on it, and one language throughout is what keeps both
from guessing. What a *person* reads is a different question, and the answer
comes from their system, not from ours.

The English string is also the lookup key. That has three consequences worth
having: the source stays readable without a table of invented message ids, a
missing translation degrades to English instead of to a blank or a key, and a
message that changes in English simply falls back until someone translates it
again — which is louder than silently showing stale text.

    from nexgen_core.i18n import t
    print(t("Could not reach {remote}", remote=remote))

Nothing here touches logs, details or identifiers. A log line is read by
whoever is debugging, and translating it would only make it harder to search.
"""
from __future__ import annotations

import os
from typing import Any

#: The language the source is written in, and the fallback for everything.
SOURCE_LANGUAGE = "en"

#: The catalogs that ship with the engine. A language absent from here simply
#: reads English; that is a missing translation, not a fault.
_CATALOGS: dict[str, dict[str, str]] = {}

_language: str | None = None


def _load_catalog(language: str) -> dict[str, str]:
    """Loads a catalog on demand, once."""
    if language in _CATALOGS:
        return _CATALOGS[language]
    catalog: dict[str, str] = {}
    if language != SOURCE_LANGUAGE:
        try:
            module = __import__(f"nexgen_core.locales.{language}", fromlist=["MESSAGES"])
            loaded = getattr(module, "MESSAGES", {})
            if isinstance(loaded, dict):
                catalog = {str(k): str(v) for k, v in loaded.items()}
        except (ImportError, AttributeError):
            catalog = {}
    _CATALOGS[language] = catalog
    return catalog


def _from_environment() -> str | None:
    """The language the system says this person reads.

    `NEXGEN_LANG` wins because someone who sets it has chosen deliberately.
    After that it is the usual POSIX chain. A value like `it_IT.UTF-8` names
    Italian; only the first two letters matter to us.
    """
    for name in ("NEXGEN_LANG", "LC_ALL", "LC_MESSAGES", "LANG"):
        raw = os.environ.get(name)
        if not raw:
            continue
        code = raw.split(".")[0].split("_")[0].strip().lower()
        if not code:
            continue
        if code in ("c", "posix"):
            return SOURCE_LANGUAGE
        return code
    return None


def _from_platform() -> str | None:
    """Windows does not set LANG, so ask the system directly."""
    try:
        import ctypes

        windll = getattr(ctypes, "windll", None)
        if windll is None:
            return None
        import locale

        language_id = windll.kernel32.GetUserDefaultUILanguage()
        name = locale.windows_locale.get(language_id)
        return name.split("_")[0].lower() if name else None
    except Exception:  # noqa: BLE001 - never let a locale probe break a command
        return None


def detect_language() -> str:
    """Which language to speak, decided once per process."""
    global _language
    if _language is None:
        _language = _from_environment() or _from_platform() or SOURCE_LANGUAGE
    return _language


def set_language(language: str | None) -> None:
    """Forces a language, or returns to detecting it. For tests and for `--lang`."""
    global _language
    _language = language.strip().lower() if language else None


def available_languages() -> list[str]:
    """The languages a translation ships for, English included."""
    from pathlib import Path

    locales_dir = Path(__file__).resolve().parent / "locales"
    found = {SOURCE_LANGUAGE}
    if locales_dir.is_dir():
        found.update(
            path.stem for path in locales_dir.glob("*.py") if not path.stem.startswith("_")
        )
    return sorted(found)


def t(message: str, /, **fields: Any) -> str:
    """The message, in the reader's language, with its fields filled in.

    Formatting happens after translation so a translator can move the fields
    around: word order is not the same in every language, and a translation
    that cannot reorder is a translation that reads wrong.
    """
    language = detect_language()
    translated = _load_catalog(language).get(message, message) if language != SOURCE_LANGUAGE else message
    if not fields:
        return translated
    try:
        return translated.format(**fields)
    except (KeyError, IndexError):
        # A translation naming a field that no longer exists must not take a
        # command down. Fall back to the source, which is always in step.
        try:
            return message.format(**fields)
        except (KeyError, IndexError):
            return message
