"""Text spliced into a PowerShell script is quoted the way PowerShell reads quotes."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import notify  # noqa: E402
from nexgen_core.processes import powershell_literal  # noqa: E402


@pytest.mark.parametrize("raw,literal", [
    ("plain", "'plain'"),
    ("it's", "'it''s'"),
    ("l’aggiornamento", "'l’’aggiornamento'"),
    ("‘a‛ ‚", "'‘‘a‛‛ ‚‚'"),
    ("", "''"),
])
def test_the_literal_doubles_every_character_powershell_reads_as_a_quote(raw, literal):
    assert powershell_literal(raw) == literal


def test_a_typographic_apostrophe_cannot_end_the_notification_string(monkeypatch):
    sent = []
    monkeypatch.setattr(notify.shutil, "which", lambda name: "powershell.exe")
    monkeypatch.setattr(notify, "_run", lambda command: sent.append(command) or True)
    assert notify._windows("Titolo d’esempio", "l’aggiornamento'; Remove-Item x; '")
    script = sent[0][-1]
    assert "'Titolo d’’esempio'" in script
    assert "'l’’aggiornamento''; Remove-Item x; '''" in script
