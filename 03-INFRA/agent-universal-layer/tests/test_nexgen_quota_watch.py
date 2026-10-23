"""The 80% and 95% alerts: one per provider, window and band, through the megaphone.

The megaphone is replaced by a recorder here. What is under test is the
decision, not the delivery: which readings raise an alert, which stay quiet, and
that the 95% alert is never swallowed by the 80% one that came before it.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from nexgen_core import i18n, quota, quota_watch

READ_AT = datetime(2026, 10, 23, 11, 21, 43, tzinfo=timezone.utc).timestamp()
NOW = READ_AT + 240.532


@pytest.fixture(autouse=True)
def _english_notes():
    """Pin the source language: the alert sentences are asserted below."""
    i18n.set_language("en")
    yield
    i18n.set_language(None)


class RecordingMegaphone:
    """A megaphone that remembers the keys it was asked to send."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    def send_alert(self, title: str, message: str, action: str | None = None,
                   alert_key: str | None = None) -> bool:
        self.sent.append({"title": title, "message": message, "action": action, "key": alert_key})
        return True


def codex_percent(home: Path, percent: float, *, window: int = 720,
                  resets: int = int(READ_AT) + 3600) -> None:
    """A Codex session whose own reading is `percent` of a `window`-minute plan."""
    folder = home / ".codex" / "sessions" / "2026" / "10" / "23"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "rollout-2026-10-23.jsonl").write_text(json.dumps({
        "timestamp": datetime.fromtimestamp(READ_AT, timezone.utc).isoformat().replace("+00:00", "Z"),
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "rate_limits": {
                "limit_id": "codex",
                "primary": {"used_percent": percent, "window_minutes": window, "resets_at": resets},
                "secondary": None,
                "credits": {"has_credits": False, "balance": None},
                "plan_type": "free",
            },
        },
    }) + "\n", encoding="utf-8")


def declared_ceiling(home: Path, *, spent: float, ceiling: float) -> None:
    """A harness archive with one paid session, and the ceiling it is measured against."""
    store = home / ".local" / "share" / "opencode" / "opencode.db"
    store.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(store)
    con.execute(
        "create table session_v2 (id text primary key, parent_id text, cost real, "
        "time_updated integer, model text)"
    )
    con.execute(
        "insert into session_v2 values (?, null, ?, ?, ?)",
        ("s1", spent, int((NOW - 600) * 1000), json.dumps({"providerID": "opencode-go"})),
    )
    con.commit()
    con.close()
    path = home / "KnowledgeVault" / "03-INFRA" / "agent-universal-layer" / "quota.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"limits:\n  opencode-go:\n    5h: {ceiling}\n", encoding="utf-8")


def test_nothing_is_sent_below_the_first_threshold(tmp_path):
    codex_percent(tmp_path, 59.0)
    phone = RecordingMegaphone()

    answer = quota_watch.check_and_alert(home=tmp_path, megaphone=phone, now=NOW)

    assert answer["judged"] == []
    assert phone.sent == []


def test_eighty_percent_raises_one_alert_with_the_provider_and_the_window(tmp_path):
    codex_percent(tmp_path, 86.0)
    phone = RecordingMegaphone()

    quota_watch.check_and_alert(home=tmp_path, megaphone=phone, now=NOW)

    assert len(phone.sent) == 1
    alert = phone.sent[0]
    assert alert["key"] == "quota:Codex (free):12h:80"
    assert alert["title"] == "Quota at 86%: Codex (free)"
    assert "has used 86% of its 12h window" in alert["message"]
    assert "it resets in" in alert["message"]
    assert "nexgen quota" in alert["action"]


def test_the_band_is_the_highest_threshold_reached(tmp_path):
    codex_percent(tmp_path, 97.0)
    phone = RecordingMegaphone()

    quota_watch.check_and_alert(home=tmp_path, megaphone=phone, now=NOW)

    assert len(phone.sent) == 1
    assert phone.sent[0]["key"].endswith(":95")


def test_the_ninety_five_alert_has_its_own_key_so_eighty_cannot_silence_it(tmp_path):
    """The debounce is per key: one key for both bands would lose the urgent one."""
    eighty = quota_watch.band_of(80.0)
    ninety = quota_watch.band_of(95.0)
    assert (eighty, ninety) == (80.0, 95.0)

    keys = {
        f"quota:Codex (free):12h:{band:.0f}"
        for band in (eighty, ninety)
    }
    assert len(keys) == 2


def test_a_declared_ceiling_makes_a_paid_lane_raise_the_alert_too(tmp_path):
    declared_ceiling(tmp_path, spent=9.0, ceiling=10.0)
    phone = RecordingMegaphone()

    quota_watch.check_and_alert(home=tmp_path, megaphone=phone, now=NOW)

    keys = [alert["key"] for alert in phone.sent]
    assert "quota:OpenCode Go:5h:80" in keys


def test_a_paid_lane_with_no_declared_ceiling_stays_quiet(tmp_path):
    declared_ceiling(tmp_path, spent=9.0, ceiling=10.0)
    (tmp_path / "KnowledgeVault" / "03-INFRA" / "agent-universal-layer" / "quota.yaml").unlink()
    phone = RecordingMegaphone()

    quota_watch.check_and_alert(home=tmp_path, megaphone=phone, now=NOW)

    assert phone.sent == []


def test_a_reading_older_than_its_window_never_raises_anything(tmp_path):
    codex_percent(tmp_path, 100.0, window=300)
    phone = RecordingMegaphone()

    quota_watch.check_and_alert(home=tmp_path, megaphone=phone, now=READ_AT + 6 * 3600)

    assert phone.sent == []


def test_an_alert_without_a_reset_time_does_not_invent_one(tmp_path):
    codex_percent(tmp_path, 86.0, resets=0)
    phone = RecordingMegaphone()

    quota_watch.check_and_alert(home=tmp_path, megaphone=phone, now=NOW)

    assert phone.sent[0]["message"] == "Codex (free) has used 86% of its 12h window."


def test_an_unreadable_machine_is_silence_and_not_a_crash(tmp_path):
    phone = RecordingMegaphone()

    answer = quota_watch.check_and_alert(home=tmp_path / "nowhere", megaphone=phone, now=NOW)

    assert answer == {"ok": True, "judged": []}
    assert phone.sent == []


def test_the_thresholds_have_one_owner():
    """The watch, the report and the doctor read the same two numbers."""
    assert quota.ALERT_AT == (80.0, 95.0)
