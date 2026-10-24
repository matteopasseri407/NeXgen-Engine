"""Pinning dello split tools/shell dopo il ritiro a favore di OpenClaw.

Fissa il comportamento del modulo shell ritirato: render_menu stabile e
avviso OpenClaw, run_shell ritorna esito con avviso, firme pubbliche invariate.
Solo input finti, solo tmp_path, mai shell reale, mai rete.
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
    """Isola la shell dai percorsi reali."""
    _no_real_shell_no_network(monkeypatch)


def test_render_menu_stabile() -> None:
    first = shell_mod.render_menu()
    second = shell_mod.render_menu()
    assert first == second
    assert "openclaw" in first.lower() or "retired" in first.lower() or "ritirata" in first.lower()
    for num, name, desc in shell_mod.MENU_ACTIONS:
        assert name in ("openclaw",)
        assert f"[{num}]" in ("[0]",)
        assert len(desc) > 0
    assert len(first.splitlines()) >= 1


def test_run_shell_su_comandi_finti(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    _fake_shell_env(monkeypatch)
    rc = shell_mod.run_shell(vault_data=tmp_path)
    assert rc == 0
    out = capsys.readouterr().out
    assert "openclaw" in out.lower() or "retired" in out.lower() or "ritirata" in out.lower()
    assert len(out.strip()) > 0


def test_run_shell_interrotto_ritorna_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_shell_env(monkeypatch)
    assert shell_mod.run_shell(vault_data=tmp_path) == 0


def test_default_scorciatoie_su_comandi_finti(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_shell_env(monkeypatch)
    sh = shell_mod.NeXgenShell(vault_data=None)
    assert sh.default("1") is False
    assert sh.default("2") is False
    assert sh.default("7") is False
    assert sh.default("") is False
    assert sh.do_exit() is True
    assert sh.do_quit() is True


def test_a_windows_path_keeps_its_backslashes(monkeypatch: pytest.MonkeyPatch, pretend_os) -> None:
    """Lo split dei sottocomandi non deve mangiare le barre rovesciate."""
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
