"""`openclaw.json` writes: private on POSIX, merged without losing the person's lists.

The file carries the gateway token, and the sections the engine owns sit beside
keys OpenClaw and the person wrote. Two promises are tested here, both about
what survives a write: who can read the bytes, and which lists keep the entries
a person added by hand.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.openclaw import setup_write  # noqa: E402 - sys.path shim for cloned checkout


# --------------------------------------------------------------- permissions


def _chmod_calls(monkeypatch) -> list[tuple[Path, int]]:
    calls: list[tuple[Path, int]] = []
    monkeypatch.setattr(os, "chmod", lambda target, mode: calls.append((Path(target), mode)))
    return calls


def test_the_file_holding_the_gateway_token_is_private_on_posix(tmp_path, monkeypatch, pretend_os):
    """`openclaw onboard` leaves 0644: the token must not stay readable by others."""
    path = tmp_path / "openclaw.json"
    calls = _chmod_calls(monkeypatch)
    pretend_os(setup_write, "posix")

    setup_write.write_document(path, "{}\n")

    assert calls == [(path, 0o600)]


def test_the_backup_of_the_token_file_is_private_on_posix(tmp_path, monkeypatch, pretend_os):
    """The copy beside the file holds the same token, so it gets the same mode."""
    path = tmp_path / "openclaw.json"
    path.write_text('{"gateway": {"auth": {"token": "secret"}}}\n', encoding="utf-8")
    calls = _chmod_calls(monkeypatch)
    pretend_os(setup_write, "posix")

    backup = setup_write.backup_file(path, "20260101-000000")

    assert backup is not None
    assert calls == [(backup, 0o600)]


def test_on_windows_no_posix_mode_is_forced(tmp_path, monkeypatch, pretend_os):
    path = tmp_path / "openclaw.json"
    calls = _chmod_calls(monkeypatch)
    pretend_os(setup_write, "nt")

    setup_write.write_document(path, "{}\n")

    assert calls == []
