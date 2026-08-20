"""Il trasporto desktop: quando c'è uno schermo, e cosa succede se non c'è.

Nessun test qui fa comparire una notifica vera. Ciò che va garantito è che un
sistema senza schermo non venga nemmeno tentato, e che un notificatore che
fallisce o si pianta non trascini con sé l'allarme: un avviso che non si può
mostrare non vale un errore, perché il messaggio viaggia comunque sugli altri
canali e nel log.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import notify


def test_a_headless_linux_session_is_not_even_attempted(monkeypatch):
    monkeypatch.setattr(notify.sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    # Un server raggiunto via SSH non ha uno schermo: provarci è un fallimento
    # garantito che rallenta soltanto l'allarme.
    assert notify.desktop_is_available() is False


def test_a_missing_notifier_is_not_an_error(monkeypatch):
    monkeypatch.setattr(notify.sys, "platform", "linux")
    monkeypatch.setattr(notify.shutil, "which", lambda _name: None)
    assert notify.send_desktop("Titolo", "Corpo") is False


def test_a_notifier_that_fails_does_not_raise(monkeypatch):
    monkeypatch.setattr(notify.sys, "platform", "linux")
    monkeypatch.setattr(notify.shutil, "which", lambda _name: "/usr/bin/notify-send")

    def explode(*_args, **_kwargs):
        raise OSError("nessun bus di sessione")

    monkeypatch.setattr(notify.subprocess, "run", explode)
    assert notify.send_desktop("Titolo", "Corpo") is False


def test_a_notifier_that_hangs_is_given_up_on(monkeypatch):
    monkeypatch.setattr(notify.sys, "platform", "linux")
    monkeypatch.setattr(notify.shutil, "which", lambda _name: "/usr/bin/notify-send")

    def hang(*_args, **kwargs):
        assert kwargs.get("timeout"), "ogni notifica deve avere un limite di tempo"
        raise subprocess.TimeoutExpired(cmd="notify-send", timeout=kwargs["timeout"])

    monkeypatch.setattr(notify.subprocess, "run", hang)
    assert notify.send_desktop("Titolo", "Corpo") is False


def test_an_empty_message_is_not_shown(monkeypatch):
    called = False

    def record(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("non si mostra una notifica vuota")

    monkeypatch.setattr(notify.subprocess, "run", record)
    assert notify.send_desktop("   ", "  ") is False
    assert called is False


def test_quotes_in_the_message_cannot_escape_the_script(monkeypatch):
    """Un apostrofo in un messaggio non deve diventare codice.

    I messaggi arrivano dai controlli e possono contenere percorsi e nomi di
    file scelti dall'utente: su macOS e Windows finiscono dentro uno script,
    e lì una virgoletta non protetta è un'iniezione.
    """
    seen: list[list[str]] = []
    monkeypatch.setattr(notify.shutil, "which", lambda _name: "/usr/bin/osascript")
    monkeypatch.setattr(
        notify.subprocess, "run",
        lambda cmd, **_k: seen.append(cmd) or subprocess.CompletedProcess(cmd, 0, b"", b""),
    )
    monkeypatch.setattr(notify.sys, "platform", "darwin")

    notify.send_desktop('Un "titolo"', 'con "virgolette"')

    script = seen[0][-1]
    assert '\\"titolo\\"' in script
    assert '\\"virgolette\\"' in script
