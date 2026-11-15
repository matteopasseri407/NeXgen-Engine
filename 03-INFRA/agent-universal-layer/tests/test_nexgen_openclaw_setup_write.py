"""`openclaw.json` changes: only the sections that differ, and the person's lists kept.

The file carries the gateway token, and the sections the engine owns sit beside
keys OpenClaw and the person wrote. Two promises are tested here, both about
what survives a change: which sections are handed to OpenClaw at all, and which
lists keep the entries a person added by hand.

Nothing here writes an `openclaw.json`: `openclaw config patch` owns the atomic
publish, the backup ring and the refusal that leaves the file alone. The patch
document it is handed, and the verdict OpenClaw returns, are tested beside this in
`test_nexgen_openclaw_config_write.py`.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.openclaw import setup_write  # noqa: E402 - sys.path shim for cloned checkout


# ------------------------------------------------------------ the patch sent


def test_only_the_sections_that_differ_are_sent():
    """OpenClaw merges what it is given: unchanged sections are not re-asserted."""
    current = {"gateway": {"port": 18789}, "plugins": {"allow": ["mine"]}}
    proposed = {"gateway": {"port": 18789}, "plugins": {"allow": ["mine", "nexgen"]}}

    merged = setup_write.merged_document(current, proposed)
    patch, replace_paths = setup_write.patch_document(current, merged)

    assert patch == {"plugins": {"allow": ["mine", "nexgen"]}}
    assert replace_paths == ["plugins"]


def test_a_document_already_in_force_sends_nothing():
    """The second identical run has no patch to hand over, so nothing is written."""
    current = {"gateway": {"port": 18789}}

    patch, replace_paths = setup_write.patch_document(current, dict(current))

    assert patch == {}
    assert replace_paths == []


def test_a_retired_key_is_removed_by_replacing_its_section():
    """A merge into the file would keep `security`/`ask`; a replaced section does not."""
    current = {"tools": {"exec": {"security": "allowlist", "ask": "on-miss"}}}
    proposed = {"tools": {"exec": {"mode": "auto"}}}

    merged = setup_write.merged_document(current, proposed)
    patch, replace_paths = setup_write.patch_document(current, merged)

    assert patch["tools"]["exec"] == {"mode": "auto"}
    assert "tools" in replace_paths


def test_a_missing_file_is_sent_whole():
    """There is nothing to merge into, so every section is the engine's."""
    merged = setup_write.merged_document(None, {"gateway": {"port": 18789}, "plugins": {"allow": []}})

    patch, replace_paths = setup_write.patch_document(None, merged)

    assert patch == {"gateway": {"port": 18789}, "plugins": {"allow": []}}
    assert replace_paths == ["gateway", "plugins"]


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
    current = {"agents": {"entries": {"main": {"tools": {"agentToAgent": {"send": ["sviluppo", "personale"]}}}}}}
    proposed = {"agents": {"entries": {"main": {}, "sviluppo": {}}}}

    send = setup_write.merged_document(current, proposed)["agents"]["entries"]["main"]["tools"]["agentToAgent"]["send"]

    assert send == ["sviluppo", "personale"]


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


def test_the_legacy_exec_pair_goes_when_the_proposal_writes_the_mode():
    """OpenClaw refuses `mode` beside `security`/`ask` in the same exec object.

    `zod-schema.agent-runtime-iDxKMLd-.mjs:403-415` (2026.10.6) adds the issue
    "mode cannot be combined with security or ask in the same exec object", and
    `config validate` exits 1 on it. The merge keeps every key the person wrote,
    so without this pass a file that never saw `openclaw doctor --fix` could
    never be validated again.
    """
    current = {"tools": {"exec": {"security": "allowlist", "ask": "on-miss"}}}
    proposed = {"tools": {"exec": {"mode": "auto"}}}

    merged = setup_write.merged_document(current, proposed)

    assert merged["tools"]["exec"] == {"mode": "auto"}, "the replacement replaces"


def test_the_legacy_exec_pair_stays_when_the_proposal_writes_no_mode():
    """A2: the engine proposes no mode over the person's, so nothing is retired either."""
    current = {"tools": {"exec": {"security": "allowlist", "ask": "on-miss"}}}
    proposed = {"tools": {"exec": {}}}

    merged = setup_write.merged_document(current, proposed)

    assert merged["tools"]["exec"] == {"security": "allowlist", "ask": "on-miss"}


def test_an_exec_object_never_carries_a_mode_and_a_legacy_key():
    """The property the schema asks for, checked over the whole result."""
    for current in (
        {"tools": {"exec": {"security": "deny"}}},
        {"tools": {"exec": {"ask": "always"}}},
        {"tools": {"exec": {"security": "full", "ask": "off", "strictInlineEval": True}}},
        {},
    ):
        merged = setup_write.merged_document(current, {"tools": {"exec": {"mode": "auto"}}})
        block = merged["tools"]["exec"]
        assert block["mode"] == "auto"
        assert "security" not in block and "ask" not in block, current


def test_the_models_entries_a_squad_wrote_into_the_old_key_are_taken_out():
    """OpenClaw refuses the whole file over that key (`models: Unrecognized key: entries`).

    An earlier engine wrote the squad's `models` table there. No OpenClaw version reads
    it, so removing it heals the file and costs nobody a key.
    """
    current = {
        "models": {
            "providers": {"ollama": {"models": [{"id": "qwen3:8b"}]}},
            "entries": {"anthropic/claude-opus-5-5": {"agentRuntime": {"id": "claude-cli"}}},
        },
        "agents": {"entries": {"main": {"model": "anthropic/claude-opus-5-5"}}},
    }
    before = copy.deepcopy(current)

    merged = setup_write.merged_document(current, {"agents": {"entries": {}}})

    assert "entries" not in merged["models"]
    assert merged["models"]["providers"] == {"ollama": {"models": [{"id": "qwen3:8b"}]}}
    assert merged["agents"]["entries"]["main"]["model"] == "anthropic/claude-opus-5-5"
    assert current == before
