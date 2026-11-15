"""The doctor line about a second registration for the gateway.

The facts come from `openclaw gateway status --deep --json`, so they are handed
to the check here as a list; nothing in this file starts a process or reads a
machine. What is pinned is the engine's half: when it speaks, what it says, and
that it never turns a registration it did not create into a failure.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import i18n  # noqa: E402
from nexgen_core.openclaw.second_service import CHECK_ID, check  # noqa: E402
from nexgen_core.report import Severity  # noqa: E402


@pytest.fixture(autouse=True)
def _english():
    i18n.set_language("en")
    yield
    i18n.set_language(None)

STARTUP_ENTRY = {
    "platform": "win32",
    "label": "OpenClaw Gateway",
    "detail": r"startup: C:\Users\user\Startup\OpenClaw Gateway.vbs",
    "scope": "user",
    "marker": "openclaw",
    "legacy": False,
}


def test_one_service_means_no_line_at_all() -> None:
    assert check([]) is None
    assert check(None) is None


def test_the_line_names_the_registration_and_the_command_that_removes_it() -> None:
    outcome = check([STARTUP_ENTRY])

    assert outcome is not None
    assert outcome.id == CHECK_ID
    assert "OpenClaw Gateway" in outcome.message
    assert r"C:\Users\user\Startup\OpenClaw Gateway.vbs" in outcome.message
    assert "one port" in outcome.message
    assert outcome.action is not None
    assert "Remove-Item -LiteralPath" in outcome.action
    assert "Keep it if it is a second profile you meant" in outcome.action


def test_the_line_is_a_warning_because_the_managed_service_still_works() -> None:
    """A second registration is work for a person, not a broken gateway."""
    outcome = check([STARTUP_ENTRY])

    assert outcome is not None
    assert outcome.severity is Severity.WARN


def test_a_registration_with_no_removal_command_still_points_at_openclaw_doctor() -> None:
    outcome = check([{"platform": "win32", "label": "mystery", "detail": "(not inspected)",
                      "scope": "user"}])

    assert outcome is not None
    assert outcome.action is not None
    assert "openclaw doctor" in outcome.action


def test_the_line_speaks_italian_when_that_is_the_language() -> None:
    i18n.set_language("it")

    outcome = check([STARTUP_ENTRY])

    assert outcome is not None
    assert "Un'altra registrazione" in outcome.message
    assert "Tienila se è un secondo profilo che volevi" in (outcome.action or "")
