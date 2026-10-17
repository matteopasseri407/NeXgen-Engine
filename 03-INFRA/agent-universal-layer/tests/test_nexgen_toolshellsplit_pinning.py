"""Pinning dello split tools/shell prima del refactor.

Fissa il comportamento attuale (non quello desiderato): render_menu stabile
(due chiamate danno lo stesso esito), run_shell su comandi finti (cmdloop
finto, nessuna shell reale), firme pubbliche invariate. Solo input finti,
solo tmp_path, mai shell reale, mai rete.
"""
from __future__ import annotations

import inspect
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.tools import shell as shell_mod  # noqa: E402

pytestmark = pytest.mark.filterwarnings("ignore")


def _no_real_shell_no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Solo finti: mai shell reale, mai sottoprocessi reali."""

    def _boom(*args, **kwargs):
        raise AssertionError("shell reale o rete toccata")

    monkeypatch.setattr(os, "system", _boom)
    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(subprocess, "Popen", _boom)


def _fake_shell_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isola la shell dai percorsi reali: readline finto, render finti."""
    _no_real_shell_no_network(monkeypatch)
    monkeypatch.setattr(shell_mod, "_setup_readline", lambda: None)
    monkeypatch.setattr(shell_mod, "render_info", lambda *a, **k: "FAKE-INFO")
    monkeypatch.setattr(shell_mod, "safe_text", lambda s: s)


def test_render_menu_stabile() -> None:
    first = shell_mod.render_menu()
    second = shell_mod.render_menu()
    assert first == second
    assert "SELECTABLE ACTIONS" in first
    for num, name, desc in shell_mod.MENU_ACTIONS:
        assert name in first, f"voce menu mancante: {name}"
        assert f"[{num}]" in first, f"scorciatoia mancante: {num}"
    # 1 intestazione + 1 separatore + N voci + 1 separatore.
    assert len(first.splitlines()) == len(shell_mod.MENU_ACTIONS) + 3


def test_run_shell_su_comandi_finti(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_shell_env(monkeypatch)
    seen: dict = {}

    def _fake_cmdloop(self) -> None:
        seen["called"] = True

    monkeypatch.setattr(shell_mod.NeXgenShell, "cmdloop", _fake_cmdloop)
    rc = shell_mod.run_shell(vault_data=tmp_path)
    assert rc == 0
    assert seen.get("called") is True


def test_run_shell_interrotto_ritorna_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_shell_env(monkeypatch)

    def _raise_interrupt(self) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(shell_mod.NeXgenShell, "cmdloop", _raise_interrupt)
    assert shell_mod.run_shell(vault_data=tmp_path) == 0


def test_default_scorciatoie_su_comandi_finti(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_shell_env(monkeypatch)
    sh = shell_mod.NeXgenShell(vault_data=None)
    chiamate: list = []
    monkeypatch.setattr(
        shell_mod.NeXgenShell, "do_doctor", lambda self, arg="": chiamate.append(("doctor", arg))
    )
    monkeypatch.setattr(
        shell_mod.NeXgenShell, "do_sync", lambda self, arg="": chiamate.append(("sync", arg))
    )
    monkeypatch.setattr(
        shell_mod.NeXgenShell, "do_info", lambda self, arg="": chiamate.append(("info", arg))
    )
    assert sh.default("1") is False
    assert chiamate[-1][0] == "doctor"
    assert sh.default("2") is False
    assert chiamate[-1][0] == "sync"
    assert sh.default("7") is False
    assert chiamate[-1][0] == "info"
    assert sh.default("") is False


def test_a_windows_path_keeps_its_backslashes(monkeypatch: pytest.MonkeyPatch, pretend_os) -> None:
    """Lo split dei sottocomandi non deve mangiare le barre rovesciate.

    In modalità POSIX `C:\\tools\\run.bat` diventa `C: ools` piu' un tab, quindi
    il sottocomando fallisce su un percorso che non esiste. Su Windows lo split
    non-POSIX tiene le barre e lascia le virgolette nei token: una coppia
    esterna viene tolta, come per il verify di patch.py.
    """
    from nexgen_core import processes

    pretend_os(processes, "nt")
    assert shell_mod.split_command_line('"C:\\tools\\my skill\\run.bat" --now') == [
        "C:\\tools\\my skill\\run.bat", "--now",
    ]
    assert shell_mod.split_command_line("plain two") == ["plain", "two"]


def test_a_posix_command_line_keeps_the_posix_split(monkeypatch: pytest.MonkeyPatch, pretend_os) -> None:
    from nexgen_core import processes

    pretend_os(processes, "posix")
    assert shell_mod.split_command_line('"/opt/x/my skill" --now') == ["/opt/x/my skill", "--now"]


def test_firme_pubbliche_invariate() -> None:
    assert list(inspect.signature(shell_mod.render_menu).parameters) == []
    assert list(inspect.signature(shell_mod.run_shell).parameters) == ["vault_data"]
    assert list(inspect.signature(shell_mod.NeXgenShell.__init__).parameters) == [
        "self",
        "vault_data",
    ]
    assert list(inspect.signature(shell_mod.NeXgenShell.default).parameters) == [
        "self",
        "line",
    ]
    assert list(inspect.signature(shell_mod.NeXgenShell.do_info).parameters) == [
        "self",
        "arg",
    ]
    assert list(inspect.signature(shell_mod.NeXgenShell.do_exit).parameters) == [
        "self",
        "arg",
    ]
