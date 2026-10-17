"""Pinning dello split now prima del refactor.

Fissa il comportamento attuale (non quello desiderato): get_agent_now_data
su ora finta stabile ritorna 13 campi deterministici senza mai toccare la
rete, NTP finto linux e windows coerenti col chiamante, firme pubbliche
invariate. Solo orologi finti, solo subprocess/winreg finti, MAI rete,
MAI NTP vero.
"""
from __future__ import annotations

import datetime
import inspect
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.tools import now as now_mod  # noqa: E402

pytestmark = pytest.mark.filterwarnings("ignore")

FIXED = datetime.datetime(
    2026, 10, 15, 12, 34, 56,
    tzinfo=datetime.timezone(datetime.timedelta(hours=2)),
)

FAKE_NTP = {
    "timezone": "Europe/Rome",
    "ntp_synchronized": "yes",
    "ntp_enabled": "yes",
    "can_ntp": "yes",
    "local_rtc": "no",
}


class _FrozenDT(datetime.datetime):
    @classmethod
    def now(cls, tz=None):  # noqa: ANN001
        if tz is None:
            return FIXED
        return FIXED.astimezone(tz)


def _freeze_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(now_mod.datetime, "datetime", _FrozenDT)


def _stub_ntp(monkeypatch: pytest.MonkeyPatch, payload: dict | None = None) -> dict:
    payload = dict(payload if payload is not None else FAKE_NTP)

    def _fake() -> dict:
        return dict(payload)

    for name in (
        "get_ntp_status_linux",
        "get_ntp_status_windows",
        "_get_ntp_status_linux_impl",
        "_get_ntp_status_windows_impl",
        "_ntp_status_linux_impl",
        "_ntp_status_windows_impl",
    ):
        if hasattr(now_mod, name):
            monkeypatch.setattr(now_mod, name, _fake)
    return payload


def _no_real_ntp(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*args, **kwargs):  # noqa: ANN001, ANN202
        raise AssertionError("rete/NTP vero toccato")

    monkeypatch.setattr(now_mod.subprocess, "run", _boom)


def test_get_agent_now_data_su_ora_finta_stabile(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_clock(monkeypatch)
    _no_real_ntp(monkeypatch)
    _stub_ntp(monkeypatch)
    first = now_mod.get_agent_now_data()
    second = now_mod.get_agent_now_data()
    assert first == second
    expected_keys = {
        "source", "local_time", "utc_time", "timezone", "epoch_seconds",
        "date", "time", "year", "weekday", "ntp_synchronized",
        "ntp_enabled", "can_ntp", "local_rtc",
    }
    assert set(first.keys()) == expected_keys
    assert first["source"] == "system_clock"
    assert first["timezone"] == "Europe/Rome"
    assert datetime.datetime.fromisoformat(first["local_time"]).astimezone(
        datetime.UTC) == FIXED.astimezone(datetime.UTC)
    assert first["epoch_seconds"] == int(FIXED.timestamp())
    assert first["date"] == "2026-10-15"
    assert first["year"] == 2026
    assert first["weekday"] == FIXED.strftime("%A")
    assert first["ntp_synchronized"] == "yes"
    assert first["ntp_enabled"] == "yes"
    assert first["can_ntp"] == "yes"
    assert first["local_rtc"] == "no"
    # utc coerente con l'ora finta
    assert first["utc_time"] == FIXED.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def test_ntp_finto_linux_coerente(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze_clock(monkeypatch)
    out = (
        "Timezone=Europe/Rome\n"
        "NTPSynchronized=yes\n"
        "NTP=yes\n"
        "CanNTP=yes\n"
        "LocalRTC=no\n"
    )

    def _fake_run(*args, **kwargs):  # noqa: ANN001, ANN202
        return subprocess.CompletedProcess(args, 0, out, "")

    monkeypatch.setattr(now_mod.subprocess, "run", _fake_run)
    info = now_mod.get_ntp_status_linux()
    assert info["timezone"] == "Europe/Rome"
    assert info["ntp_synchronized"] == "yes"
    assert info["ntp_enabled"] == "yes"
    assert info["can_ntp"] == "yes"
    assert info["local_rtc"] == "no"

    # Coerenza col chiamante: su linux get_agent_now_data usa timedatectl finto.
    monkeypatch.setattr(now_mod.sys, "platform", "linux")
    data = now_mod.get_agent_now_data()
    assert data["timezone"] == "Europe/Rome"
    assert data["ntp_synchronized"] == "yes"
    assert data["local_rtc"] == "no"


def test_ntp_finto_windows_coerente(monkeypatch: pytest.MonkeyPatch) -> None:
    import types

    _freeze_clock(monkeypatch)

    def _boom(*args, **kwargs):  # noqa: ANN001, ANN202
        raise AssertionError("timedatectl non deve girare su win32 finto")

    monkeypatch.setattr(now_mod.subprocess, "run", _boom)

    class _FakeKey:
        def __enter__(self):
            return self

        def __exit__(self, *exc):  # noqa: ANN001
            return False

    fake_winreg = types.ModuleType("winreg")
    fake_winreg.HKEY_LOCAL_MACHINE = 0
    fake_winreg.OpenKey = lambda *a, **k: _FakeKey()
    fake_winreg.QueryValueEx = lambda key, name: ("NTP", None)
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)

    info = now_mod.get_ntp_status_windows()
    assert info["ntp_enabled"] == "yes"
    assert info["can_ntp"] == "yes"
    assert info["ntp_synchronized"] == "yes"

    # Coerenza col chiamante: su win32 finto get_agent_now_data usa W32Time finto.
    monkeypatch.setattr(now_mod.sys, "platform", "win32")
    data = now_mod.get_agent_now_data()
    assert data["ntp_enabled"] == "yes"
    assert data["ntp_synchronized"] == "yes"
    assert data["can_ntp"] == "yes"


def test_firme_pubbliche_invariate() -> None:
    assert list(inspect.signature(now_mod.get_ntp_status_linux).parameters) == []
    assert list(inspect.signature(now_mod.get_ntp_status_windows).parameters) == []
    assert list(inspect.signature(now_mod.get_agent_now_data).parameters) == []
    assert list(inspect.signature(now_mod.format_human).parameters) == ["data"]
    assert list(inspect.signature(now_mod.format_shell).parameters) == ["data"]
    assert list(inspect.signature(now_mod.main).parameters) == ["argv"]
