"""Il toast che Windows mostra davvero, e il pallone solo quando l'API non c'è.

Nessun test qui fa comparire una notifica vera: ciò che va garantito è la catena.
Prima il toast, che resta nel centro notifiche; solo se l'identità dell'applicazione
non si può creare, il vecchio pallone del tray, che Windows 11 mostra tardi o mai.
E l'identità è la parte che si sbaglia in silenzio: se la scorciatoia e la notifica
non portano lo stesso AppUserModelID, Windows scarta il messaggio senza dire niente,
ed è esattamente il guasto che questa via esiste per chiudere.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import notify, windows_toast  # noqa: E402 - cloned checkout, sys.path first


@pytest.fixture
def windows(monkeypatch):
    """Un Windows con PowerShell, senza toccare la macchina che esegue i test."""
    monkeypatch.setattr(notify.sys, "platform", "win32")
    monkeypatch.setattr(
        notify.shutil, "which", lambda name: "powershell" if name == "powershell" else None
    )


def _script_calls(monkeypatch, *, creates_shortcut: bool, home: Path) -> list[str]:
    """Sostituisce PowerShell con un finto esecutore che registra cosa gli è stato chiesto."""
    calls: list[str] = []

    def fake_run(command, **_kwargs):
        calls.append(command[-1])
        if creates_shortcut and "CreateShortcut" in command[-1]:
            link = windows_toast.shortcut_path(home)
            link.parent.mkdir(parents=True, exist_ok=True)
            link.write_text("", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(notify.subprocess, "run", fake_run)
    return calls


def test_the_first_toast_creates_the_identity_and_then_speaks(monkeypatch, tmp_path, windows):
    """Senza identità Windows scarta la notifica: la prima volta si crea, poi si parla."""
    monkeypatch.setattr(windows_toast, "_write_registration", lambda: True)
    calls = _script_calls(monkeypatch, creates_shortcut=True, home=tmp_path)

    assert windows_toast.show("NeXgen: prova", "Corpo della prova", home=tmp_path) is True

    assert len(calls) == 2, calls
    assert "CreateShortcut" in calls[0]
    assert "ToastNotificationManager" in calls[1]
    assert "ShowBalloonTip" not in calls[1]


def test_a_machine_that_already_has_the_identity_sends_a_single_toast(monkeypatch, tmp_path, windows):
    calls = _script_calls(monkeypatch, creates_shortcut=False, home=tmp_path)
    monkeypatch.setattr(windows_toast, "identity_present", lambda home=None: True)

    assert windows_toast.show("NeXgen: prova", "Corpo", home=tmp_path) is True

    assert len(calls) == 1, calls
    assert "ToastNotificationManager" in calls[0]


def test_the_toast_and_the_registration_carry_the_same_identity(monkeypatch, tmp_path, windows):
    """Un AUMID diverso fra scorciatoia e notifica è il guasto silenzioso da evitare."""
    assert windows_toast.REGISTRY_PATH.endswith(windows_toast.AUMID)
    assert f"'{windows_toast.AUMID}'" in windows_toast.toast_script("titolo", "corpo")


def test_without_an_identity_no_toast_is_attempted(monkeypatch, tmp_path, windows):
    """Se l'identità non nasce, la risposta è no: la notifica non parte per essere scartata."""
    monkeypatch.setattr(windows_toast, "_write_registration", lambda: False)
    calls = _script_calls(monkeypatch, creates_shortcut=False, home=tmp_path)

    assert windows_toast.show("NeXgen: prova", "Corpo", home=tmp_path) is False
    assert not any("ToastNotificationManager" in call for call in calls), calls


def test_a_system_without_the_toast_api_is_not_attempted(monkeypatch, tmp_path):
    monkeypatch.setattr(notify.sys, "platform", "linux")
    called: list[str] = []
    monkeypatch.setattr(notify.subprocess, "run", lambda cmd, **_k: called.append(cmd) or subprocess.CompletedProcess(cmd, 0, b"", b""))

    assert windows_toast.show("NeXgen: prova", "Corpo", home=tmp_path) is False
    assert called == []


def test_the_text_cannot_escape_the_script(monkeypatch, tmp_path, windows):
    """Un apostrofo in un avviso non deve diventare codice: il testo è un letterale."""
    calls = _script_calls(monkeypatch, creates_shortcut=False, home=tmp_path)
    monkeypatch.setattr(windows_toast, "identity_present", lambda home=None: True)

    windows_toast.show("L'aggiornamento non è passato", "Chiudi l'app e riprova", home=tmp_path)

    script = calls[-1]
    assert "'L''aggiornamento non è passato'" in script
    assert "'Chiudi l''app e riprova'" in script


def test_unregister_removes_the_start_menu_entry(monkeypatch, tmp_path):
    """Un disinstallazione non deve lasciare un'icona che non fa più niente."""
    link = windows_toast.shortcut_path(tmp_path)
    link.parent.mkdir(parents=True)
    link.write_text("", encoding="utf-8")
    monkeypatch.setattr(windows_toast, "_registry", lambda: False)

    assert windows_toast.unregister(tmp_path) is True
    assert link.exists() is False


def test_a_click_opens_the_report_and_nothing_else(monkeypatch, tmp_path):
    """Il clic non deve poter fare niente di diverso dal mostrare il referto."""
    shim_dir = tmp_path / ".local" / "bin"
    shim_dir.mkdir(parents=True)
    (shim_dir / "nexgen.cmd").write_text("@echo off\n", encoding="utf-8")

    target, arguments = windows_toast._shortcut_target(tmp_path)
    assert target == str(shim_dir / "nexgen.cmd")
    assert arguments == "doctor"

    empty = tmp_path / "senza-launcher"
    empty.mkdir()
    target, arguments = windows_toast._shortcut_target(empty)
    assert target.lower().endswith("powershell.exe")
    assert arguments == "-NoProfile -Command nexgen doctor"


def test_the_toast_comes_before_the_balloon(monkeypatch, windows):
    """L'ordine è la correzione: il pallone non deve arrivare per primo a nessuno."""
    monkeypatch.setattr(windows_toast, "show", lambda *a, **k: True)
    monkeypatched_balloon: list[str] = []
    monkeypatch.setattr(notify, "_balloon", lambda title, body: monkeypatched_balloon.append(title) or True)

    assert notify.send_desktop("NeXgen: prova", "Corpo") is True
    assert monkeypatched_balloon == []


def test_the_balloon_is_the_fallback_when_the_toast_api_is_unavailable(monkeypatch, windows):
    monkeypatch.setattr(windows_toast, "show", lambda *a, **k: False)
    seen: dict = {}

    def fake_run(command, **kwargs):
        seen["script"] = command[-1]
        seen["timeout"] = kwargs.get("timeout")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(notify.subprocess, "run", fake_run)

    assert notify.send_desktop("NeXgen: prova", "Corpo") is True
    assert "ShowBalloonTip" in seen["script"]
    assert seen["timeout"] > 10
