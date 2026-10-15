"""Pinning di describe_host prima dello split in helper privato.

Fissa il comportamento attuale (non quello desiderato): su macchina finta
ritorna le righe attese, stabile su doppia chiamata, firme pubbliche
invariate. Solo tmp_path e monkeypatch, nessuna rete, nessuna home vera.
"""
from __future__ import annotations

import inspect
import os
import platform
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import nexgen_core.first_run as first_run_mod  # noqa: E402
from nexgen_core.first_run import describe_host  # noqa: E402


def _fake_windows(monkeypatch) -> None:
    """Una macchina finta e deterministica: niente file veri, niente home vera."""
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(platform, "release", lambda: "10")
    monkeypatch.setattr(os, "cpu_count", lambda: 8)
    monkeypatch.setattr(first_run_mod, "_memory_gb", lambda: 32)


def test_describe_host_fake_windows_returns_expected_parts(tmp_path, monkeypatch):
    """Su macchina finta ritorna le righe attese."""
    fake_home = tmp_path / "home-finta"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    _fake_windows(monkeypatch)

    assert describe_host() == "10, 8 core, 32 GB RAM"


def test_describe_host_fake_minimal_returns_system_only(tmp_path, monkeypatch):
    """Senza dettagli noti resta solo il nome del sistema, niente placeholder."""
    fake_home = tmp_path / "home-finta"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    monkeypatch.setattr(platform, "system", lambda: "MyOS-9")
    monkeypatch.setattr(os, "cpu_count", lambda: None)
    monkeypatch.setattr(first_run_mod, "_memory_gb", lambda: None)

    described = describe_host()
    assert described == "MyOS-9"
    assert "[" not in described
    assert "FILL IN" not in described


def test_describe_host_stable_on_double_call(tmp_path, monkeypatch):
    """Due chiamate di fila sulla stessa macchina finta danno lo stesso testo."""
    fake_home = tmp_path / "home-finta"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    _fake_windows(monkeypatch)

    first = describe_host()
    second = describe_host()
    assert first == second == "10, 8 core, 32 GB RAM"


def test_public_signatures_unchanged():
    assert list(inspect.signature(describe_host).parameters) == []
    assert inspect.signature(describe_host).return_annotation in (str, "str")
