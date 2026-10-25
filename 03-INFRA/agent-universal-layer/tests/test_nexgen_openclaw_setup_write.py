"""`openclaw.json` writes: private on POSIX, merged without losing the person's lists.

The file carries the gateway token, and the sections the engine owns sit beside
keys OpenClaw and the person wrote. Two promises are tested here, both about
what survives a write: who can read the bytes, and which lists keep the entries
a person added by hand.
"""
from __future__ import annotations

import copy
import os
import sys
from pathlib import Path

import pytest

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


def test_the_document_never_inherits_the_previous_mode(tmp_path, monkeypatch):
    """The old 0644 was carried across the rename: the temp must already be private."""
    seen: dict = {}

    def writer(path, text, *, preserve_mode=True, **kwargs):
        seen["preserve_mode"] = preserve_mode
        Path(path).write_text(text, encoding="utf-8")

    monkeypatch.setattr(setup_write, "atomic_write_text", writer)

    setup_write.write_document(tmp_path / "openclaw.json", "{}\n")

    assert seen["preserve_mode"] is False


@pytest.mark.skipif(os.name == "nt", reason="mode bits are a POSIX concept")
def test_a_0644_file_becomes_0600_on_posix(tmp_path):
    path = tmp_path / "openclaw.json"
    path.write_text("{}\n", encoding="utf-8")
    os.chmod(path, 0o644)

    setup_write.write_document(path, "{}\n")

    assert path.stat().st_mode & 0o777 == 0o600


def test_the_backup_of_the_token_file_is_private_on_posix(tmp_path, monkeypatch, pretend_os):
    """The copy beside the file holds the same token, so it gets the same mode."""
    path = tmp_path / "openclaw.json"
    path.write_text('{"gateway": {"auth": {"token": "secret"}}}\n', encoding="utf-8")
    calls = _chmod_calls(monkeypatch)
    pretend_os(setup_write, "posix")

    backup = setup_write.backup_file(path, "20260101-000000")

    assert backup is not None
    assert len(calls) == 1
    published, mode = calls[0]
    assert mode == 0o600
    assert published.parent == path.parent
    assert published.name.startswith(backup.name + "."), "the mode is set on the bytes before they are published"


def test_on_windows_no_posix_mode_is_forced(tmp_path, monkeypatch, pretend_os):
    path = tmp_path / "openclaw.json"
    calls = _chmod_calls(monkeypatch)
    pretend_os(setup_write, "nt")

    setup_write.write_document(path, "{}\n")

    assert calls == []


def test_a_crash_during_restore_never_truncates_the_file(tmp_path, monkeypatch):
    """`copy2` wrote into the destination: a crash left openclaw.json half written."""
    path = tmp_path / "openclaw.json"
    backup = tmp_path / "openclaw.json.pre-nexgen-1"
    path.write_text('{"current": 1}\n', encoding="utf-8")
    backup.write_text('{"previous": 1}\n', encoding="utf-8")

    def crash(*args, **kwargs):
        raise OSError("power cut")

    monkeypatch.setattr(setup_write.os, "replace", crash)

    with pytest.raises(OSError):
        setup_write.restore_file(path, backup)

    assert path.read_text(encoding="utf-8") == '{"current": 1}\n'
    assert not list(tmp_path.glob("*.tmp")), "the unpublished temp file was left behind"


def test_a_crash_during_backup_leaves_no_partial_copy(tmp_path, monkeypatch):
    path = tmp_path / "openclaw.json"
    path.write_text('{"gateway": {"auth": {"token": "secret"}}}\n', encoding="utf-8")

    def crash(*args, **kwargs):
        raise OSError("power cut")

    monkeypatch.setattr(setup_write.os, "replace", crash)

    with pytest.raises(OSError):
        setup_write.backup_file(path, "20260101-000000")

    assert not list(tmp_path.glob("openclaw.json.pre-nexgen-*"))
    assert not list(tmp_path.glob("*.tmp"))
    assert "secret" in path.read_text(encoding="utf-8")


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


def test_an_agent_the_person_added_to_the_agent_to_agent_allow_list_stays():
    """OpenClaw needs both agents in `allow`: replacing it cuts the person's own out."""
    current = {"tools": {"agentToAgent": {"enabled": True, "allow": ["main", "capo", "personale"]}}}
    proposed = {"tools": {"agentToAgent": {"enabled": True, "allow": ["main", "capo", "sviluppo"]}}}

    allow = setup_write.merged_document(current, proposed)["tools"]["agentToAgent"]["allow"]

    assert allow == ["main", "capo", "personale", "sviluppo"]


def test_the_allow_list_drops_the_agent_this_run_leaves_out():
    """`--no-sviluppo` creates no such agent: an id nothing declares is unroutable."""
    current = {"tools": {"agentToAgent": {"enabled": True, "allow": ["main", "capo", "personale", "sviluppo"]}}}
    proposed = {
        "agents": {"entries": {"main": {}, "capo": {}}},
        "tools": {"agentToAgent": {"enabled": True, "allow": ["main", "capo"]}},
    }

    allow = setup_write.merged_document(current, proposed)["tools"]["agentToAgent"]["allow"]

    assert allow == ["main", "capo", "personale"]


def test_the_allow_list_keeps_the_agent_this_run_creates():
    """The rule is about an agent that does not exist, not about the id itself."""
    current = {"tools": {"agentToAgent": {"enabled": True, "allow": ["main", "sviluppo"]}}}
    proposed = {
        "agents": {"entries": {"main": {}, "sviluppo": {}}},
        "tools": {"agentToAgent": {"enabled": True, "allow": ["main", "sviluppo"]}},
    }

    allow = setup_write.merged_document(current, proposed)["tools"]["agentToAgent"]["allow"]

    assert allow == ["main", "sviluppo"]


def test_a_send_target_the_run_leaves_out_goes_too():
    """The same dead reference in the per-agent partner list, and only that one."""
    current = {"agents": {"entries": {"main": {"tools": {"agentToAgent": {"send": ["*", "sviluppo", "personale"]}}}}}}
    proposed = {"agents": {"entries": {"main": {"tools": {"agentToAgent": {"send": ["capo"]}}}}}}

    send = setup_write.merged_document(current, proposed)["agents"]["entries"]["main"]["tools"]["agentToAgent"]["send"]

    assert send == ["*", "personale", "capo"]


def test_the_proposal_is_left_untouched_while_the_agent_is_taken_out():
    """A section only the proposal carries is its own object: the merge must not edit it.

    `merged_document` promises that neither input is modified, and the plan is read
    after the write: an edit in place would change the document the caller still
    holds.
    """
    proposed = {
        "agents": {"entries": {"main": {"tools": {"agentToAgent": {"send": ["*", "sviluppo"]}}}}},
        "tools": {"agentToAgent": {"enabled": True, "allow": ["main", "sviluppo"]}},
    }
    before = copy.deepcopy(proposed)

    merged = setup_write.merged_document(None, proposed)

    assert merged["tools"]["agentToAgent"]["allow"] == ["main"]
    assert proposed == before


def test_the_persons_bindings_are_kept_and_the_new_one_added():
    person = {"agentId": "support", "match": {"channel": "discord", "accountId": "*"}}
    new = {"agentId": "main", "match": {"channel": "telegram", "accountId": "*"}}
    current = {"bindings": [person]}
    proposed = {"bindings": [new]}

    merged = setup_write.merged_document(current, proposed)

    assert merged["bindings"] == [person, new]


def test_an_identical_binding_is_not_added_twice():
    binding = {"agentId": "main", "match": {"channel": "telegram", "accountId": "*"}}

    merged = setup_write.merged_document({"bindings": [binding]}, {"bindings": [dict(binding)]})

    assert merged["bindings"] == [binding]


def test_a_list_is_still_replaced_where_the_proposal_owns_it():
    """The union is for the permission lists only: a title is not a set."""
    current = {"agents": {"entries": {"main": {"name": "old", "tags": ["a"]}}}}
    proposed = {"agents": {"entries": {"main": {"name": "new", "tags": ["b"]}}}}

    merged = setup_write.merged_document(current, proposed)["agents"]["entries"]["main"]

    assert merged == {"name": "new", "tags": ["b"]}



# --------------------------------------------------------------- retirement


def test_legacy_bare_false_tool_search_is_retired():
    """An earlier engine wrote `toolSearch: False`: setup removes the bare boolean."""
    current = {"tools": {"toolSearch": False, "profile": "minimal"}}
    proposed = {"tools": {"profile": "standard"}}

    merged = setup_write.merged_document(current, proposed)

    assert "toolSearch" not in merged["tools"]
    assert merged["tools"]["profile"] == "standard"


def test_user_configured_tool_search_object_is_preserved():
    """An object written by the person (e.g. `mode` or `enabled: False`) is preserved."""
    current = {"tools": {"toolSearch": {"enabled": False}}}
    proposed = {"tools": {"profile": "standard"}}

    merged = setup_write.merged_document(current, proposed)

    assert merged["tools"]["toolSearch"] == {"enabled": False}


def test_user_configured_tool_search_boolean_true_is_preserved():
    """If the person explicitly pinned `toolSearch: True`, it is preserved."""
    current = {"tools": {"toolSearch": True}}
    proposed = {}

    merged = setup_write.merged_document(current, proposed)

    assert merged["tools"]["toolSearch"] is True
