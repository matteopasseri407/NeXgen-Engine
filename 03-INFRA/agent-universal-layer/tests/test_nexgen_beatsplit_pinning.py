"""Pinning dello split liveness/depwatch di beat prima del refactor.

Fissa il comportamento attuale (non quello desiderato): check_liveness
su stato finto ritorna esito senza mai toccare la home reale,
run_dependency_watch con resolver finti e stabile (due chiamate danno lo
stesso esito), firme pubbliche invariate. Solo tmp_path, solo stati
finti, nessuna rete, nessun ciclo reale.
"""
from __future__ import annotations

import inspect
import sys
import time
import types
from dataclasses import dataclass
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import beat  # noqa: E402

pytestmark = pytest.mark.filterwarnings("ignore")


def _make_heartbeat(tmp_path: Path) -> beat.Heartbeat:
    home = tmp_path / "home"
    vault = tmp_path / "vault"
    state = tmp_path / "state"
    engine = tmp_path / "engine"
    for d in (home, vault, state, engine):
        d.mkdir(parents=True, exist_ok=True)
    return beat.Heartbeat(state_dir=state, vault_data=vault, engine_root=engine, home=home)


def test_check_liveness_su_stato_finto_ritorna_esito(tmp_path: Path) -> None:
    hb = _make_heartbeat(tmp_path)
    # Nessun file: esito False, messaggio non vuoto, nessuna eccezione.
    ok, msg = hb.check_liveness()
    assert ok is False
    assert isinstance(msg, str) and msg.strip()

    # Ciclo recente finto: esito True.
    hb.record_liveness()
    ok, msg = hb.check_liveness()
    assert ok is True
    assert isinstance(msg, str) and msg.strip()

    # File corrotto finto: esito False, mai eccezione, un solo alert debounced.
    hb.liveness_file.write_text("not-a-float\n", encoding="utf-8")
    calls: list[dict] = []
    hb.megaphone.send_alert = lambda **kw: calls.append(kw) or False  # type: ignore[method-assign]
    ok, msg = hb.check_liveness()
    assert ok is False
    assert isinstance(msg, str) and msg.strip()
    assert len(calls) == 1
    assert calls[0].get("alert_key") == "guard_liveness_corrupt"


def test_check_liveness_stale_avvisa_una_volta(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hb = _make_heartbeat(tmp_path)
    stale_ts = time.time() - 5 * 3600
    hb.state_dir.mkdir(parents=True, exist_ok=True)
    hb.liveness_file.write_text(f"{stale_ts}\n", encoding="utf-8")
    monkeypatch.setattr(beat, "_just_booted", lambda grace: False)
    calls: list[dict] = []
    hb.megaphone.send_alert = lambda **kw: calls.append(kw) or False  # type: ignore[method-assign]
    ok, msg = hb.check_liveness()
    assert ok is False
    assert isinstance(msg, str) and msg.strip()
    assert len(calls) == 1
    assert calls[0].get("alert_key") == "guard_stale"


def test_system_uptime_uses_the_windows_tick_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """MUST-06: on Windows the uptime comes from GetTickCount64, not /proc/uptime."""
    import ctypes

    fake_ctypes = types.SimpleNamespace(c_uint64=ctypes.c_uint64, windll=types.SimpleNamespace(
        kernel32=types.SimpleNamespace(GetTickCount64=_TickCount64(60_000)),
    ))
    monkeypatch.setattr(beat, "os", types.SimpleNamespace(name="nt"))
    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)

    assert beat._system_uptime_seconds() == 60.0
    assert beat._just_booted(300.0) is True
    assert beat._just_booted(30.0) is False


class _TickCount64:
    """GetTickCount64 as ctypes really hands it over: without a declared
    restype the 64-bit result is read as a signed C int (32 bits)."""

    def __init__(self, milliseconds: int) -> None:
        self.milliseconds = milliseconds
        self.restype = None

    def __call__(self) -> int:
        import ctypes

        if self.restype is ctypes.c_uint64:
            return self.milliseconds
        return ctypes.c_int(self.milliseconds).value


def test_a_month_of_windows_uptime_is_not_a_fresh_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    """Past 2**31 ms (24.8 days) the truncated reading went negative: always
    "less than the grace window", so a stalled sync never alarmed again."""
    import ctypes

    thirty_days_ms = 30 * 86_400 * 1000
    fake_ctypes = types.SimpleNamespace(
        c_uint64=ctypes.c_uint64,
        windll=types.SimpleNamespace(kernel32=types.SimpleNamespace(GetTickCount64=_TickCount64(thirty_days_ms))),
    )
    monkeypatch.setattr(beat, "os", types.SimpleNamespace(name="nt"))
    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)

    assert beat._system_uptime_seconds() == thirty_days_ms / 1000.0
    assert beat._just_booted(300.0) is False


def test_stale_liveness_just_after_a_boot_does_not_alarm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A night of downtime reads as "machine just started", not "sync stalled"."""
    hb = _make_heartbeat(tmp_path)
    hb.state_dir.mkdir(parents=True, exist_ok=True)
    hb.liveness_file.write_text(f"{time.time() - 10 * 3600}\n", encoding="utf-8")
    monkeypatch.setattr(beat, "_system_uptime_seconds", lambda: 300.0, raising=False)
    calls: list[dict] = []
    hb.megaphone.send_alert = lambda **kw: calls.append(kw) or False  # type: ignore[method-assign]

    ok, msg = hb.check_liveness()

    assert ok is True, "the guard has not had a chance to run yet"
    assert calls == [], "the just-booted grace must silence the stall alarm"
    assert isinstance(msg, str) and msg.strip()


@dataclass
class _FakeFinding:
    stale: bool


@dataclass
class _FakeDepwatchResult:
    wrote: bool
    findings: list


def _stub_depwatch_stack(monkeypatch: pytest.MonkeyPatch, wrote: bool = True) -> dict:
    findings = [_FakeFinding(stale=False), _FakeFinding(stale=True)]

    def _fake_run_depwatch(*, vault_data=None, state_dir=None, **kw):  # noqa: ANN001, ANN202
        return _FakeDepwatchResult(wrote=wrote, findings=list(findings))

    monkeypatch.setattr(beat, "run_depwatch", _fake_run_depwatch)
    import nexgen_core.thirdparty_bump as bump_mod
    import nexgen_core.thirdparty_guard as guard_mod

    monkeypatch.setattr(guard_mod, "run_guardian", lambda *a, **k: {"ok": True, "stub": True})
    monkeypatch.setattr(bump_mod, "read_guard_payload", lambda state_dir: {})
    monkeypatch.setattr(
        bump_mod, "auto_apply", lambda *a, **k: {"ok": True, "stub": True}
    )
    return {"wrote": wrote}


def test_run_dependency_watch_stabile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hb = _make_heartbeat(tmp_path)
    _stub_depwatch_stack(monkeypatch)
    first = hb.run_dependency_watch()
    second = hb.run_dependency_watch()
    assert first == second
    assert first["ok"] is True
    assert first["wrote"] is True
    assert first["stale"] == 1
    assert first["guard"] == {"ok": True, "stub": True}
    assert first["auto_applied"] == {"ok": True, "stub": True}


def test_run_dependency_watch_non_alza_mai(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hb = _make_heartbeat(tmp_path)

    def _boom(*, vault_data=None, state_dir=None, **kw):  # noqa: ANN001, ANN202
        raise RuntimeError("finto-offline")

    monkeypatch.setattr(beat, "run_depwatch", _boom)
    result = hb.run_dependency_watch()
    assert result == {"ok": False, "error": "finto-offline"}


def test_firme_pubbliche_invariate() -> None:
    assert list(inspect.signature(beat.auto_update_enabled).parameters) == ["environ"]
    assert list(inspect.signature(beat.Heartbeat.check_liveness).parameters) == ["self"]
    assert list(inspect.signature(beat.Heartbeat.run_dependency_watch).parameters) == ["self"]
    assert list(inspect.signature(beat.Heartbeat.run_beat).parameters) == ["self"]
    assert list(inspect.signature(beat.Heartbeat.run_self_upgrade).parameters) == ["self"]
