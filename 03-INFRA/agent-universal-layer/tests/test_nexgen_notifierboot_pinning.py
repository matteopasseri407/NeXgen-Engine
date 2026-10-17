"""Pinning del comportamento attuale di notifier_boot su tmp_path con env finto.

Fissa: _ensure_posix_boot_check e _ensure_windows_boot_check scrivono solo
quando il contenuto cambia, ensure_shell_hook e' idempotente, nessuna
scrittura fuori tmp_path. Non tocca la home reale: ogni test isola
NEXGEN_HOME/HOME/AGENT_STATE_DIR/APPDATA dentro tmp_path e mocka
_run_quiet/shutil.which (niente schtasks/systemctl reali).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.tools import notifier_boot  # noqa: E402
from nexgen_core.tools import update_notifier as notifier  # noqa: E402

MARKER = "NeXgen Engine update notice"


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    appdata = tmp_path / "appdata"
    appdata.mkdir(exist_ok=True)
    monkeypatch.setenv("NEXGEN_HOME", str(h))
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("USERPROFILE", str(h))
    monkeypatch.setenv("AGENT_STATE_DIR", str(state))
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("LOCALAPPDATA", str(appdata))
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(notifier_boot.shutil, "which", lambda name: None)
    return h


def _posix_artifacts(h: Path):
    return (
        h / ".config" / "autostart" / "nexgen-update-check.desktop",
        h / ".config" / "systemd" / "user" / "nexgen-update-check.service",
        h / ".config" / "systemd" / "user" / "nexgen-update-check.timer",
    )


def test_posix_boot_check_writes_only_when_changed(home, monkeypatch):
    monkeypatch.setattr(notifier_boot, "_run_quiet", lambda argv, timeout=60: (0, "enabled"))
    first = notifier_boot._ensure_posix_boot_check(home)
    desktop, service, timer = _posix_artifacts(home)
    assert desktop.is_file() and service.is_file() and timer.is_file()
    assert first, "la prima ensure deve riportare note di scrittura"
    snapshot = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (desktop, service, timer)}
    second = notifier_boot._ensure_posix_boot_check(home)
    assert isinstance(second, list)
    for p, (data, mtime) in snapshot.items():
        assert p.read_bytes() == data, f"contenuto cambiato senza motivo: {p}"
        assert p.stat().st_mtime_ns == mtime, f"riscrittura inutile: {p}"
    assert all(str(p).startswith(str(home)) for p in (desktop, service, timer))


def test_windows_boot_check_writes_only_when_changed(home, monkeypatch):
    vbs = os.path.join(str(home), ".local", "state", "nexgen-update-check-hidden.vbs")

    def fake_run(argv, timeout=60):
        if len(argv) >= 2 and argv[0] == "schtasks.exe" and argv[1] == "/Query":
            return (0, vbs)
        return (0, "ok")

    monkeypatch.setattr(notifier_boot, "_run_quiet", fake_run)
    first = notifier_boot._ensure_windows_boot_check(str(home))
    assert Path(vbs).is_file()
    assert any("wrapper" in n for n in first), f"attesa nota di scrittura wrapper: {first}"
    content = Path(vbs).read_text(encoding="utf-8")
    mtime = Path(vbs).stat().st_mtime_ns
    second = notifier_boot._ensure_windows_boot_check(str(home))
    assert Path(vbs).read_text(encoding="utf-8") == content
    assert Path(vbs).stat().st_mtime_ns == mtime, "il wrapper non deve essere riscritto se identico"
    assert second == ["[autostart] logon task already active"]
    dest = notifier_boot._windows_startup_copy(os.environ.get("APPDATA"), str(home))
    assert str(Path(dest)) .startswith(str(home.parent)), "il fallback deve restare dentro tmp_path"
    assert not Path(dest).exists(), "a task attivo nessun fallback deve essere copiato"


def test_ensure_shell_hook_idempotent(home):
    first = notifier.ensure_shell_hook(home)
    assert first != ["[shell-hook] already present"], "la prima installazione deve scrivere"
    marked = [
        p for p in home.rglob("*")
        if p.is_file() and MARKER in p.read_text(encoding="utf-8", errors="replace")
    ]
    assert marked, "lo shell hook non ha scritto nulla sotto home"
    for p in marked:
        assert p.read_text(encoding="utf-8").count(MARKER) == 1
    mtimes = {p: p.stat().st_mtime_ns for p in marked}
    second = notifier.ensure_shell_hook(home)
    assert second == ["[shell-hook] already present"]
    for p in marked:
        assert p.read_text(encoding="utf-8").count(MARKER) == 1, f"blocco duplicato in {p}"
        assert p.stat().st_mtime_ns == mtimes[p], f"riscrittura inutile: {p}"


def test_no_writes_outside_tmp_path(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    appdata = tmp_path / "appdata"
    appdata.mkdir(exist_ok=True)
    monkeypatch.setenv("NEXGEN_HOME", str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("AGENT_STATE_DIR", str(state))
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("LOCALAPPDATA", str(appdata))
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(notifier_boot.shutil, "which", lambda name: None)

    written: list[str] = []
    orig_write = notifier_boot._write_if_different
    orig_write_text = notifier_boot._write_text_if_different
    orig_append = notifier_boot._append_once

    def _check(p) -> None:
        assert str(p).startswith(str(tmp_path)), f"scrittura fuori tmp_path: {p}"

    def w1(p, c):
        _check(p)
        written.append(str(p))
        return orig_write(p, c)

    def w2(p, c):
        _check(p)
        written.append(str(p))
        return orig_write_text(p, c)

    def a1(p, b):
        _check(p)
        written.append(str(p))
        return orig_append(p, b)

    monkeypatch.setattr(notifier_boot, "_write_if_different", w1)
    monkeypatch.setattr(notifier_boot, "_write_text_if_different", w2)
    monkeypatch.setattr(notifier_boot, "_append_once", a1)

    vbs = os.path.join(str(home), ".local", "state", "nexgen-update-check-hidden.vbs")
    monkeypatch.setattr(
        notifier_boot, "_run_quiet",
        lambda argv, timeout=60: (0, vbs) if "schtasks.exe" in argv[:1] and "/Query" in argv else (0, "enabled"),
    )
    notifier_boot._ensure_posix_boot_check(home)
    notifier_boot._ensure_windows_boot_check(str(home))
    notifier.ensure_shell_hook(home)

    assert written, "le lane avrebbero dovuto scrivere almeno un file (wrapper rotto?)"
    assert list(outside.rglob("*")) == [], "scrittura fuori dalla home esplicita"
    for p in tmp_path.rglob("*"):
        if p.is_file():
            assert str(p).startswith(str(tmp_path))
