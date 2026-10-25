"""An update on a machine that reads the Vault never tries to publish the pin.

The engine pin lives in the Vault, and `nexgen update` publishes it with `vault-push`.
On a server the clone is a reader: the same release that made that rule true makes the
update fail at the last step and roll itself back. The machine installs the release
and leaves the pin to the clone that owns the write path.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import updater  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core import vault_readonly  # noqa: E402 - sys.path shim for cloned checkout


def test_a_reader_machine_does_not_publish_the_pin(tmp_path, monkeypatch, capsys):
    data_repo = tmp_path / "KnowledgeVault"
    data_repo.mkdir()
    pin = data_repo / "ENGINE-PIN.txt"
    pin.write_text("vecchio\n", encoding="utf-8")
    called: list[list[str]] = []

    monkeypatch.setattr(vault_readonly, "readonly_reason", lambda *a, **k: "the server role owns this machine")
    monkeypatch.setattr(updater, "_run", lambda *a, **k: called.append(list(a[0])))

    updater._commit_split_pin(
        pin_file=pin, target_head="a" * 40, target="v2.21.3", data_repo=data_repo, entry=["nexgen"],
    )

    assert called == []
    assert pin.read_text(encoding="utf-8") == "vecchio\n"
    assert "not published from this machine" in capsys.readouterr().out


def test_a_writer_machine_still_publishes_it(tmp_path, monkeypatch):
    data_repo = tmp_path / "KnowledgeVault"
    data_repo.mkdir()
    pin = data_repo / "ENGINE-PIN.txt"
    pin.write_text("vecchio\n", encoding="utf-8")
    calls: list[list[str]] = []

    def run(argv, **kwargs):
        calls.append(list(argv))
        return type("Done", (), {"returncode": 0})()

    monkeypatch.setattr(vault_readonly, "readonly_reason", lambda *a, **k: None)
    monkeypatch.setattr(updater, "_run", run)
    monkeypatch.setattr(updater, "_assert_clean", lambda *a, **k: None)

    updater._commit_split_pin(
        pin_file=pin, target_head="b" * 40, target="v2.21.3", data_repo=data_repo, entry=["nexgen"],
    )

    assert pin.read_text(encoding="utf-8").strip() == "b" * 40
    assert calls and "push" in calls[0]
