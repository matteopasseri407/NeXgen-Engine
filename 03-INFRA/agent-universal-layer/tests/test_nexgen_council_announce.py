"""The one line a council sends when nobody is watching it.

`--notify` exists because a chat or a schedule starts the run and is gone before it
ends. These tests prove the message says how the run ended, carries the identifier
it was given, and never replaces the failure it reports; the last tests drive the
real `council.main` with a fake mode, so nothing here starts a seat.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

COUNCIL_DIR = Path(__file__).resolve().parents[2] / "agent-universal-layer" / "council"
if str(COUNCIL_DIR) not in sys.path:
    sys.path.insert(0, str(COUNCIL_DIR))

import announce  # noqa: E402 - council modules use bare imports, sys.path first
import council  # noqa: E402
from nexgen_core import i18n  # noqa: E402


@pytest.fixture(autouse=True)
def english():
    """These assertions are about the sentences, not about the host's language."""
    i18n.set_language("en")
    yield
    i18n.set_language(None)


class Recorder:
    """A megaphone that records what would have been sent."""

    def __init__(self, delivered: bool = True) -> None:
        self.calls: list[dict] = []
        self.delivered = delivered

    def send_alert(self, title, message, action=None, alert_key=None):
        self.calls.append({"title": title, "message": message, "action": action, "key": alert_key})
        return self.delivered


# ------------------------------------------------------------------ the message


def test_a_finished_run_says_what_it_concluded_and_where_the_rest_is():
    title, message, action = announce.outcome_lines(
        "brainstorm", "c1", "APPROVE", "/kept/council-q-20261024T100000Z", "",
    )

    assert title == "Council brainstorm finished"
    assert message == "Request: c1. Outcome: APPROVE."
    assert action == "Full outcome: /kept/council-q-20261024T100000Z"


def test_a_stopped_run_says_why_and_quotes_no_verdict():
    title, message, action = announce.outcome_lines("relay", "r7", "APPROVE", "", "no seats.yaml")

    assert title == "Council relay stopped"
    assert message == "Request: r7. It stopped before the end: no seats.yaml"
    assert action == ""


def test_a_run_with_nothing_to_quote_says_so_instead_of_inventing_a_verdict():
    _, message, _ = announce.outcome_lines("consult", "", "", "", "")

    assert message == "It ended without a verdict to quote."


def test_each_run_has_its_own_key_so_two_councils_in_one_hour_both_arrive():
    assert announce.alert_key("c1", "consult") != announce.alert_key("c2", "consult")
    assert announce.alert_key("", "consult") == "council:consult"


def test_the_line_goes_through_the_one_alert_surface_with_the_key_of_this_run():
    recorder = Recorder()

    line = announce.announce_outcome(
        "consult", tag="c9", summary="APPROVE=2", session_dir="/kept/x", megaphone=recorder,
    )

    assert recorder.calls == [{
        "title": "Council consult finished",
        "message": "Request: c9. Outcome: APPROVE=2.",
        "action": "Full outcome: /kept/x",
        "key": "council:c9",
    }]
    assert line.startswith("[council] notified:")


def test_a_megaphone_with_no_channel_is_a_line_and_never_an_exception():
    line = announce.announce_outcome("challenge", tag="c1", failure="boom", megaphone=Recorder(False))

    assert line == "[council] not delivered: Council challenge stopped — Request: c1. It stopped before the end: boom"


# --------------------------------------------------------------- the CLI flag


@pytest.fixture
def quiet_main(monkeypatch):
    """`council.main` with no shutdown handlers and no real mode behind it."""
    monkeypatch.setattr(council, "_install_shutdown_handlers", lambda: None)
    return council.main


@pytest.fixture
def sent(monkeypatch):
    """The announcements `council.main` would make, as a list of keyword dicts."""
    calls: list[dict] = []

    def recorder(mode, **kwargs):
        calls.append({"mode": mode, **kwargs})
        return "[council] notified: ok"

    monkeypatch.setattr(announce, "announce_outcome", recorder)
    return calls


def test_the_flag_sends_what_the_mode_recorded(monkeypatch, quiet_main, sent):
    def fake_mode(args):
        council._record("brainstorm", "/kept/session", "APPROVE")

    monkeypatch.setattr(council, "cmd_brainstorm", fake_mode)

    assert quiet_main(["brainstorm", "a question", "--notify", "--tag", "c1", "--keep-session"]) == 0

    assert sent == [{
        "mode": "brainstorm", "tag": "c1", "summary": "APPROVE",
        "session_dir": "/kept/session", "failure": "",
    }]


def test_a_session_the_run_removed_is_not_pointed_at(monkeypatch, quiet_main, sent):
    """Without `--keep-session` the folder is gone: naming it would send a person to nothing."""
    monkeypatch.setattr(
        council, "cmd_brainstorm", lambda args: council._record("brainstorm", "/kept/session", "APPROVE"),
    )

    quiet_main(["brainstorm", "a question", "--notify"])

    assert sent[0]["session_dir"] == ""


def test_without_the_flag_nothing_is_sent(monkeypatch, quiet_main, sent):
    monkeypatch.setattr(
        council, "cmd_brainstorm", lambda args: council._record("brainstorm", None, "APPROVE"),
    )

    quiet_main(["brainstorm", "a question"])

    assert sent == []


def test_a_run_that_stops_before_its_session_exists_is_reported_and_still_fails(
    monkeypatch, quiet_main, sent,
):
    """No seats.yaml: the error is the person's answer, and the message must carry it."""
    import proposal

    monkeypatch.setattr(proposal, "SEATS_PATH", Path("/nonexistent/seats.yaml"))

    with pytest.raises(SystemExit) as stopped:
        quiet_main(["challenge", "a plan", "--notify", "--tag", "s1"])

    assert "no seats.yaml" in str(stopped.value.code)
    assert len(sent) == 1
    assert sent[0]["tag"] == "s1"
    assert sent[0]["summary"] == ""
    assert "no seats.yaml" in sent[0]["failure"]
