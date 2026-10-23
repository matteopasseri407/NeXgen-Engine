"""The alert's first transport is OpenClaw, and Telegram is not told twice.

Every test injects both the executor and the PATH lookup, because proving that
the megaphone *would have* called `openclaw` must never launch the real one on
the machine running the suite. The desktop is neutralised in the same fixture
for the same reason: a fallback that reached the real screen would be a test
with a side effect people can see.
"""
from __future__ import annotations

import logging
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import megaphone, notify
from nexgen_core.i18n import set_language
from nexgen_core.megaphone import Megaphone

OPENCLAW = "/usr/local/bin/openclaw"
TITLE = "Backup failed"
MESSAGE = "The disk is full"
ACTION = "nexgen backup --clean"
TELEGRAM_URL = "https://api.telegram.org/botsynthetic-token/sendMessage"


class Executor:
    """The injected `run`: records the argv and answers, or raises, as told."""

    def __init__(self, outcome: int | BaseException = 0) -> None:
        self.outcome = outcome
        self.calls: list[tuple[list[str], float]] = []

    def __call__(self, argv: list[str], timeout: float):
        self.calls.append((list(argv), timeout))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return SimpleNamespace(returncode=self.outcome)


class Network:
    """`urllib.request.urlopen` replaced: remembers the URLs, never dials."""

    def __init__(self) -> None:
        self.urls: list[str] = []

    def __call__(self, request, timeout=10):
        self.urls.append(getattr(request, "full_url", str(request)))
        return nullcontext(SimpleNamespace(status=200))


def present(_name: str) -> str:
    return OPENCLAW


def absent(_name: str) -> None:
    return None


@pytest.fixture(autouse=True)
def nothing_from_this_machine(monkeypatch, tmp_path):
    """A temporary HOME, no inherited transport setting, no real toast."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for name in (
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "AGENT_ALERT_WEBHOOK_URL",
        "NEXGEN_ALERT_CHANNEL",
        "NEXGEN_ALERT_TARGET",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(notify, "desktop_is_available", lambda: False)
    set_language("en")
    yield home
    set_language(None)


def build(tmp_path: Path, executor: Executor, which=present) -> Megaphone:
    return Megaphone(state_dir=tmp_path / "state", run=executor, which=which)


def test_openclaw_carries_the_alert_and_telegram_is_not_called(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXGEN_ALERT_CHANNEL", "telegram")
    monkeypatch.setenv("NEXGEN_ALERT_TARGET", "@owner")
    # Both direct-Path pieces are configured on purpose: a machine that used
    # the Bot API until now has them, so a regression here sends twice rather
    # than zero times, which is the failure this test exists to catch.
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "synthetic-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "424242")
    network = Network()
    monkeypatch.setattr(megaphone.urllib.request, "urlopen", network)
    executor = Executor()
    searched: list[str] = []

    def which(name: str) -> str | None:
        searched.append(name)
        return OPENCLAW

    notifier = Megaphone(state_dir=tmp_path / "state", run=executor, which=which)
    assert notifier.send_alert(TITLE, MESSAGE, ACTION) is True

    assert searched == ["openclaw"]
    assert len(executor.calls) == 1
    argv, timeout = executor.calls[0]
    assert argv == [
        "openclaw", "message", "send",
        "--channel", "telegram",
        "--target", "@owner",
        "--message", f"🚨 {TITLE}\n\n{MESSAGE}\n\nAction required: {ACTION}",
    ]
    assert timeout == megaphone.OPENCLAW_TIMEOUT_SECONDS
    assert "<b>" not in argv[-1]
    assert network.urls == []


def test_telegram_chat_id_is_the_target_and_the_direct_call_stays_quiet(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXGEN_ALERT_CHANNEL", "telegram")
    # No NEXGEN_ALERT_TARGET: compatibility with a machine that only ever told
    # the engine its Telegram chat.
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "424242")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "synthetic-token")
    network = Network()
    monkeypatch.setattr(megaphone.urllib.request, "urlopen", network)
    executor = Executor()

    assert build(tmp_path, executor).send_alert(TITLE, MESSAGE) is True

    assert executor.calls[0][0] == [
        "openclaw", "message", "send",
        "--channel", "telegram",
        "--target", "424242",
        "--message", f"🚨 {TITLE}\n\n{MESSAGE}",
    ]
    assert network.urls == []


@pytest.mark.parametrize(
    "outcome",
    [
        1,
        subprocess.TimeoutExpired(cmd="openclaw", timeout=20.0),
        OSError("synthetic: the shim cannot be started"),
    ],
    ids=["exit-code", "timeout", "oserror"],
)
def test_a_failed_openclaw_falls_back_to_direct_telegram(tmp_path, monkeypatch, outcome):
    monkeypatch.setenv("NEXGEN_ALERT_CHANNEL", "telegram")
    monkeypatch.setenv("NEXGEN_ALERT_TARGET", "@owner")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "synthetic-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "424242")
    network = Network()
    monkeypatch.setattr(megaphone.urllib.request, "urlopen", network)
    executor = Executor(outcome)

    assert build(tmp_path, executor).send_alert(TITLE, MESSAGE) is True

    assert len(executor.calls) == 1
    assert network.urls == [TELEGRAM_URL]


def test_a_failed_openclaw_falls_back_to_the_webhook(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXGEN_ALERT_CHANNEL", "slack")
    monkeypatch.setenv("NEXGEN_ALERT_TARGET", "channel:C1")
    monkeypatch.setenv("AGENT_ALERT_WEBHOOK_URL", "https://example.invalid/alert")
    network = Network()
    monkeypatch.setattr(megaphone.urllib.request, "urlopen", network)
    executor = Executor(1)

    assert build(tmp_path, executor).send_alert(TITLE, MESSAGE) is True

    assert network.urls == ["https://example.invalid/alert"]


def test_with_openclaw_failing_and_nothing_else_configured_the_desktop_speaks(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXGEN_ALERT_CHANNEL", "telegram")
    monkeypatch.setenv("NEXGEN_ALERT_TARGET", "@owner")
    toasts: list[tuple[str, str]] = []
    monkeypatch.setattr(notify, "desktop_is_available", lambda: True)
    monkeypatch.setattr(notify, "send_desktop", lambda title, body: toasts.append((title, body)) or True)
    executor = Executor(subprocess.TimeoutExpired(cmd="openclaw", timeout=20.0))

    assert build(tmp_path, executor).send_alert(TITLE, MESSAGE, ACTION) is True

    assert len(executor.calls) == 1
    assert toasts == [(TITLE, f"{MESSAGE}\n\nAction required: {ACTION}")]


def test_without_openclaw_the_direct_telegram_path_is_what_it_always_was(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXGEN_ALERT_CHANNEL", "telegram")
    monkeypatch.setenv("NEXGEN_ALERT_TARGET", "@owner")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "synthetic-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "424242")
    network = Network()
    monkeypatch.setattr(megaphone.urllib.request, "urlopen", network)
    executor = Executor()

    assert build(tmp_path, executor, which=absent).send_alert(TITLE, MESSAGE) is True

    assert executor.calls == []
    assert network.urls == [TELEGRAM_URL]


def test_a_channel_without_a_destination_is_not_a_route(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXGEN_ALERT_CHANNEL", "telegram")
    network = Network()
    monkeypatch.setattr(megaphone.urllib.request, "urlopen", network)
    executor = Executor()

    # Nothing can carry it, and saying so is the honest answer.
    assert build(tmp_path, executor).send_alert(TITLE, MESSAGE) is False

    assert executor.calls == []
    assert network.urls == []


def test_a_target_without_a_channel_is_not_a_route_either(tmp_path, monkeypatch):
    # Both variables are required on purpose: routing alerts somewhere new is
    # a choice the person makes, not something one stray variable does.
    monkeypatch.setenv("NEXGEN_ALERT_TARGET", "@owner")
    network = Network()
    monkeypatch.setattr(megaphone.urllib.request, "urlopen", network)
    executor = Executor()

    assert build(tmp_path, executor).send_alert(TITLE, MESSAGE) is False

    assert executor.calls == []
    assert network.urls == []


def test_debounce_still_holds_the_second_alert(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXGEN_ALERT_CHANNEL", "telegram")
    monkeypatch.setenv("NEXGEN_ALERT_TARGET", "@owner")
    executor = Executor()
    notifier = build(tmp_path, executor)

    assert notifier.send_alert(TITLE, MESSAGE, alert_key="disk") is True
    assert notifier.send_alert(TITLE, MESSAGE, alert_key="disk") is True

    assert len(executor.calls) == 1
    assert (tmp_path / "state" / megaphone.STATE_FILE_NAME).is_file()


def test_a_failed_openclaw_logs_the_reason_class_and_no_destination(tmp_path, monkeypatch, caplog):
    marker = "synthetic-private-target"
    monkeypatch.setenv("NEXGEN_ALERT_CHANNEL", "telegram")
    monkeypatch.setenv("NEXGEN_ALERT_TARGET", marker)
    executor = Executor(OSError(f"cannot reach {marker}"))

    with caplog.at_level(logging.DEBUG, logger="nexgen_core.megaphone"):
        assert build(tmp_path, executor).send_alert(TITLE, MESSAGE) is False

    assert "OSError" in caplog.text
    assert marker not in caplog.text
