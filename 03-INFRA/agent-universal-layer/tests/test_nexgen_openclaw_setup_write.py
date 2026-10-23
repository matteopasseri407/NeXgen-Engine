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


# ------------------------------------------------------------------- merging


def test_a_tool_the_person_added_to_a_cage_is_not_removed():
    """`alsoAllow` and `deny` are sets: replacing them drops the person's tools."""
    current = {"tools": {"byProvider": {"ollama/qwen3:8b": {
        "alsoAllow": ["my_tool"], "deny": ["my_bad_tool"],
    }}}}
    proposed = {"tools": {"byProvider": {"ollama/qwen3:8b": {
        "alsoAllow": ["read"], "deny": ["write"],
    }}}}

    cage = setup_write.merged_document(current, proposed)["tools"]["byProvider"]["ollama/qwen3:8b"]

    assert cage["alsoAllow"] == ["my_tool", "read"]
    assert cage["deny"] == ["my_bad_tool", "write"]


def test_an_agent_to_agent_partner_the_person_added_stays():
    current = {"agents": {"entries": {"main": {"tools": {"agentToAgent": {"send": ["helper"]}}}}}}
    proposed = {"agents": {"entries": {"main": {"tools": {"agentToAgent": {"send": ["capo"]}}}}}}

    send = setup_write.merged_document(current, proposed)["agents"]["entries"]["main"]["tools"]["agentToAgent"]["send"]

    assert send == ["helper", "capo"]


def test_a_list_is_still_replaced_where_the_proposal_owns_it():
    """The union is for the permission lists only: a title is not a set."""
    current = {"agents": {"entries": {"main": {"name": "old", "tags": ["a"]}}}}
    proposed = {"agents": {"entries": {"main": {"name": "new", "tags": ["b"]}}}}

    merged = setup_write.merged_document(current, proposed)["agents"]["entries"]["main"]

    assert merged == {"name": "new", "tags": ["b"]}

