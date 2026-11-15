"""The Council's two privacy gates work with no external scanner installed.

gitleaks is a maintainer tool: it scans the engine's own commits, and it is not on the PATH of
the machines that use the Council. The egress gate (the brief about to reach a seat) and the
redaction of a seat's output are product behaviour, so they run on the built-in matcher and the
forms in `leak-scan/leak_patterns.yaml`. Every test here empties PATH, which is the strongest
statement available that neither gate reaches for a binary.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

COUNCIL_DIR = Path(__file__).resolve().parents[1] / "council"
if str(COUNCIL_DIR) not in sys.path:
    sys.path.insert(0, str(COUNCIL_DIR))

import session  # noqa: E402

#: A brief carrying a private key header, assembled so this test file is not itself the leak.
PRIVATE_KEY_BRIEF = "review this diff\n-----BEGIN " + "PRIVATE KEY-----\nnot a real key\n"


@pytest.fixture
def no_scanner(monkeypatch):
    """No external program can be found at all, whatever the host has installed."""
    monkeypatch.setenv("PATH", "")


def test_the_egress_gate_blocks_a_brief_with_a_private_key(no_scanner, capsys):
    with pytest.raises(SystemExit) as stopped:
        session.egress_gate(PRIVATE_KEY_BRIEF)
    assert stopped.value.code == 1
    printed = capsys.readouterr().out
    assert "the brief contains possible secrets, send blocked" in printed
    assert "not a real key" not in printed


def test_the_egress_gate_lets_a_clean_brief_through_silently(no_scanner, capsys):
    session.egress_gate("please review this function and say what breaks")
    assert capsys.readouterr().out == ""


def test_an_allowlisted_value_is_not_a_finding(no_scanner, capsys):
    session.egress_gate("the health check answers on 127.0.0.1 and documents 192.0.2.7")
    assert capsys.readouterr().out == ""


def test_generated_output_is_redacted_line_by_line(no_scanner):
    answer = "keep this line\n-----BEGIN " + "PRIVATE KEY-----\nkeep this one too\n"
    redacted, changed = session.redact_generated_output(answer)
    assert changed is True
    assert redacted == "keep this line\n[REDACTED POSSIBLE SECRET]\nkeep this one too\n"


def test_clean_generated_output_is_returned_unchanged(no_scanner):
    answer = "the review is unremarkable\nand it stays exactly as it came\n"
    assert session.redact_generated_output(answer) == (answer, False)
