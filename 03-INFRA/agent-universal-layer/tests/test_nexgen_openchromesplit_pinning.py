"""Pinning dello split open/chrome prima del refactor.

Fissa il comportamento attuale (non quello desiderato) con backend finti:
open_folder finto stabile su cartella tmp, heal_chrome finto stabile senza
mai lanciare Chrome vero, firme pubbliche invariate. Solo chiamate finte,
solo tmp_path, MAI aprire cartelle vere, MAI avviare Chrome vero, MAI rete.
"""
from __future__ import annotations

import inspect
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import nexgen_core.tools.chrome as chrome_mod  # noqa: E402
import nexgen_core.tools.open_folder as open_folder_mod  # noqa: E402


def _no_real_open_no_real_chrome(monkeypatch: pytest.MonkeyPatch) -> None:
    """Solo finti: mai comandi reali, mai rete, mai Chrome vero."""

    def _boom(*args, **kwargs):
        raise AssertionError("cartella vera / Chrome vero / rete toccata")

    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(subprocess, "Popen", _boom)
    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    import os as _os

    monkeypatch.setattr(_os, "startfile", _boom, raising=False)


def test_open_folder_finto_stabile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_real_open_no_real_chrome(monkeypatch)
    target = tmp_path / "cartella"
    target.mkdir()
    calls: list[str] = []

    import os as _os
    import shutil as _shutil

    if sys.platform == "win32":
        def _fake_startfile(path: str) -> None:
            calls.append(path)

        monkeypatch.setattr(_os, "startfile", _fake_startfile, raising=False)
    else:
        monkeypatch.setattr(_shutil, "which", lambda name: f"/usr/bin/{name}")

        def _fake_run(argv, **kwargs):
            calls.append(str(argv[-1]))
            return subprocess.CompletedProcess(argv, 0, "", "")

        monkeypatch.setattr(subprocess, "run", _fake_run)
        # open_folder legge shutil.which e subprocess.run dai propri globali
        monkeypatch.setattr(open_folder_mod.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(open_folder_mod.subprocess, "run", _fake_run)

    first = open_folder_mod.open_folder(str(target))
    second = open_folder_mod.open_folder(str(target))
    assert first == 0
    assert second == 0
    assert len(calls) == 2
    assert all(Path(c).resolve() == target.resolve() for c in calls)


def test_open_folder_rifiuta_input_non_validi(tmp_path: Path) -> None:
    assert open_folder_mod.open_folder("relative/path") == 2
    assert open_folder_mod.open_folder(str(tmp_path / "inesistente")) == 2
    not_a_dir = tmp_path / "file.txt"
    not_a_dir.write_text("x", encoding="utf-8")
    assert open_folder_mod.open_folder(str(not_a_dir)) == 2


def test_heal_chrome_finto_stabile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _no_real_open_no_real_chrome(monkeypatch)
    launches: list = []
    monkeypatch.setattr(chrome_mod, "is_cdp_up", lambda *a, **k: False)
    monkeypatch.setattr(chrome_mod, "get_profile_dir", lambda: tmp_path / "profile")
    monkeypatch.setattr(chrome_mod, "singleton_owner_pid", lambda profile: None)
    monkeypatch.setattr(chrome_mod.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(chrome_mod, "launch_chrome", lambda extra_args=None: launches.append(extra_args) or 0)

    def _fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(chrome_mod.subprocess, "run", _fake_run)

    assert chrome_mod.heal_chrome() == 0
    assert chrome_mod.heal_chrome() == 0
    assert len(launches) == 2


def test_heal_chrome_con_cdp_su_non_lancia(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_real_open_no_real_chrome(monkeypatch)

    def _boom_launch(*args, **kwargs):
        raise AssertionError("launch toccato con CDP su")

    monkeypatch.setattr(chrome_mod, "is_cdp_up", lambda *a, **k: True)
    monkeypatch.setattr(chrome_mod, "launch_chrome", _boom_launch)
    assert chrome_mod.heal_chrome() == 0


def test_firme_pubbliche_invariate() -> None:
    assert list(inspect.signature(open_folder_mod.open_folder).parameters) == ["path_str"]
    assert list(inspect.signature(open_folder_mod.main).parameters) == ["argv"]
    assert list(inspect.signature(chrome_mod.is_cdp_up).parameters) == ["timeout"]
    assert list(inspect.signature(chrome_mod.find_chrome_executable).parameters) == []
    assert list(inspect.signature(chrome_mod.get_profile_dir).parameters) == []
    assert list(inspect.signature(chrome_mod.singleton_owner_pid).parameters) == ["profile"]
    assert list(inspect.signature(chrome_mod.launch_chrome).parameters) == ["extra_args"]
    assert list(inspect.signature(chrome_mod.heal_chrome).parameters) == ["extra_args"]
    assert list(inspect.signature(chrome_mod.main).parameters) == ["argv"]


def test_split_helpers_privati_delegano() -> None:
    open_src = Path(open_folder_mod.__file__).read_text(encoding="utf-8")
    assert "def _open_folder_impl" in open_src
    open_body = open_src[open_src.index("def open_folder("):]
    assert "_open_folder_impl(" in open_body

    chrome_src = Path(chrome_mod.__file__).read_text(encoding="utf-8")
    assert "def _heal_chrome_impl" in chrome_src
    heal_body = chrome_src[chrome_src.index("def heal_chrome("):]
    assert "_heal_chrome_impl(" in heal_body
