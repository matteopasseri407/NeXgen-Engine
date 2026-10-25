"""The role a machine recorded, and the command that records it.

A machine prepared by hand never ran the installer role that writes this record,
so the engine could not tell it was the always-on gateway: its clone of the Vault
kept committing in a repository nobody there could push. `nexgen role` is the
answer a person can run on a machine that is already in service, and these tests
pin both halves of it, plus the setup recording the role it is configuring.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "03-INFRA" / "scripts"))

from nexgen_core import i18n, roles  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.cli import main  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw import setup  # noqa: E402 - sys.path shim for cloned checkout


@pytest.fixture(autouse=True)
def _a_home_of_its_own(tmp_path, monkeypatch):
    """Point the machine-local state at a scratch home: no test reads or writes the real one."""
    for name in ("XDG_STATE_HOME", "AGENT_STATE_DIR", "NEXGEN_HOME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    # The sentences under test are the engine's, so the language is pinned: a
    # machine set to Italian must not turn these assertions red.
    i18n.set_language("en")
    return tmp_path


def test_a_machine_with_no_record_says_so_and_names_the_command(capsys):
    assert main(["role"]) == 0

    printed = capsys.readouterr().out
    assert "No role is recorded" in printed
    assert "nexgen role set server" in printed


def test_setting_the_role_writes_the_record_and_the_next_read_sees_it(capsys):
    assert main(["role", "set", "server"]) == 0
    capsys.readouterr()

    written = json.loads(roles.role_record_path().read_text(encoding="utf-8"))
    assert written == {"role": "server"}

    assert main(["role"]) == 0
    assert "This machine is recorded as: server" in capsys.readouterr().out


def test_the_three_roles_are_the_ones_the_installer_accepts():
    assert set(roles.ROLES) == {"server", "node", "standalone"}


def test_an_unknown_role_is_refused_before_anything_is_written():
    with pytest.raises(SystemExit) as refused:
        main(["role", "set", "gateway"])

    assert refused.value.code == 2
    assert not roles.role_record_path().exists()
