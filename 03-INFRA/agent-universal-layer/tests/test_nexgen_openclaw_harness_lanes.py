"""Which lane each OpenClaw agent runs on, and the two blocks that make an ACP lane real.

The first group pins what the lanes are *today*, before anything here can change
them: an agent is on the Go endpoint unless the file says otherwise, the ACP
bridge is absent until something asks for it, the Claude pin is a per-model
`agentRuntime`, and two retired values are the only ones a merge removes.

The rest is the new decision: `harness:` in a squad is the only way onto an ACP
lane, it is written as a runtime block *and* a binding or not at all, `agy` is a
CLI backend and never an ACP session, and the whole-agent `{"type": "embedded"}`
key the pinned release ignores leaves a file on the next merge.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.openclaw import harness_lanes, setup_write  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.acp import (  # noqa: E402 - sys.path shim for cloned checkout
    ACP_HARNESSES,
    HARNESS_TO_ACP,
    acp_agent,
    acp_allowed_agents,
    acp_block,
    acpx_entry,
    binding,
)
from nexgen_core.openclaw.detect import Detection, Installation, LocalModels, Login  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.model_policy import runtime_model_pins  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.runtime import acp_runtime  # noqa: E402 - sys.path shim for cloned checkout

STATE = "/state"
SVILUPPO_WORKSPACE = "/state/workspace-sviluppo"
TELEGRAM = {"channel": "telegram", "accountId": "*"}
ACCEPTED = pytest.mark.parametrize("harness", ["claude", "codex", "opencode"])

SVILUPPO_ACP_BINDING = {
    "type": "acp",
    "agentId": "sviluppo",
    "match": {"channel": "telegram", "accountId": "*"},
    "acp": {"mode": "persistent", "cwd": SVILUPPO_WORKSPACE, "label": "sviluppo-opencode"},
}


def machine(*, claude: bool = False, codex: bool = False, go: bool = False, opencode: bool = False) -> Detection:
    """One machine, described by the logins and the CLI it has."""
    return Detection(
        openclaw=Installation(installed=True, version="2026.11.2", config_path=f"{STATE}/openclaw.json"),
        claude=Login(binary=claude, login=True if claude else None),
        codex=Login(binary=codex, login=True if codex else None),
        opencode_go=go,
        local=LocalModels(),
        n8n=False,
        opencode=opencode,
        opencode_command=f"{STATE}/opencode" if opencode else "",
    )


EVERYTHING = machine(claude=True, codex=True, go=True, opencode=True)


def proposal(*, workspace: str = SVILUPPO_WORKSPACE, binding_agent: str = "sviluppo") -> dict:
    """The smallest proposal that can carry an agent and one channel binding."""
    return {
        "agents": {"entries": {"sviluppo": {"name": "sviluppo", "workspace": workspace}}},
        "bindings": [{"agentId": binding_agent, "match": dict(TELEGRAM)}],
    }


# ------------------------------------------------------- today's lanes, pinned


@pytest.mark.parametrize("agent", ["main", "capo", "automazioni", "sviluppo"])
def test_today_no_agent_declares_a_harness_so_each_one_is_on_the_provider_lane(agent: str) -> None:
    """Characterisation: the Go endpoint over HTTP is what these three and `main` do."""
    squad = {"agents": {agent: {"model": "opencode-go/deepseek-v4.1-flash"}}}
    lanes = harness_lanes.lanes(squad, EVERYTHING, {agent: SVILUPPO_WORKSPACE})
    assert lanes == (
        harness_lanes.Lane(agent, "provider", "", "opencode-go/deepseek-v4.1-flash", SVILUPPO_WORKSPACE),
    )


def test_the_acp_bridge_is_absent_until_something_asks_for_it() -> None:
    """Characterisation: a machine with no OpenCode agent and no `--acpx` gets no bridge."""
    assert acp_block(machine(claude=True)) is None
    assert acp_block(machine(claude=True), acpx=True) == {
        "enabled": True, "backend": "acpx", "allowedAgents": ["claude"],
    }


def test_the_bridge_allows_the_three_harnesses_in_one_fixed_order() -> None:
    assert ACP_HARNESSES == ("claude", "codex", "opencode")
    assert acp_allowed_agents(EVERYTHING) == ["claude", "codex", "opencode"]


def test_today_opencode_in_the_bridge_needs_the_go_key_as_well_as_the_cli() -> None:
    """Characterisation of the engine's own agent: it runs DeepSeek through that key."""
    assert acp_allowed_agents(machine(opencode=True)) == []
    assert acp_allowed_agents(machine(opencode=True, go=True)) == ["opencode"]


def test_the_claude_pin_is_per_model_and_never_a_provider() -> None:
    assert runtime_model_pins(machine(claude=True)) == {
        "anthropic/*": {"agentRuntime": {"id": "claude-cli"}},
    }
    assert runtime_model_pins(machine(codex=True)) == {}


def test_the_acpx_bridge_approves_tools_and_refuses_a_prompt_nobody_can_answer() -> None:
    assert acpx_entry() == {
        "enabled": True,
        "config": {"permissionMode": "approve-all", "nonInteractivePermissions": "deny"},
    }


def test_the_two_retired_values_are_the_ones_an_earlier_engine_wrote() -> None:
    """Characterisation: a merge removes exactly these, and only while untouched."""
    assert setup_write.RETIRED_VALUES == (
        (("agents", "entries", "sviluppo", "runtime"),
         {"type": "acp", "acp": {"agent": "opencode", "backend": "acpx", "mode": "persistent"}}),
        (("tools", "toolSearch"), False),
    )


# ------------------------------------------------------- the lane decision


@ACCEPTED
def test_a_declared_harness_with_its_cli_present_is_an_acp_lane(harness: str) -> None:
    spec = {"harness": harness, "model": "anthropic/claude-opus-5-5"}
    lane = harness_lanes.lane_of("sviluppo", spec, EVERYTHING, SVILUPPO_WORKSPACE)
    assert lane == harness_lanes.Lane(
        "sviluppo", "acp", harness, "anthropic/claude-opus-5-5", SVILUPPO_WORKSPACE, "",
    )


def test_agy_is_a_cli_backend_and_never_an_acp_lane() -> None:
    lane = harness_lanes.lane_of("capo", {"harness": "agy"}, EVERYTHING, "/state/workspace-capo")
    assert lane.kind == "cli"
    assert lane.harness == "agy"
    assert lane.reason == ""


def test_none_is_the_explicit_spelling_of_the_lane_there_is_today() -> None:
    assert harness_lanes.declared_harness({"harness": " none "}) == ""
    lane = harness_lanes.lane_of("capo", {"harness": "none"}, EVERYTHING, "/w")
    assert lane == harness_lanes.Lane("capo", "provider", "", "", "/w", "")


def test_a_typo_in_the_harness_key_is_reported_and_writes_nothing() -> None:
    lane = harness_lanes.lane_of("sviluppo", {"harness": "frotz"}, EVERYTHING, SVILUPPO_WORKSPACE)
    assert lane.kind == "provider"
    assert lane.harness == "frotz"
    assert lane.reason == harness_lanes.UNKNOWN
    assert "frotz" in harness_lanes.problem(lane)


def test_a_harness_this_machine_cannot_run_is_not_applied() -> None:
    lane = harness_lanes.lane_of("sviluppo", {"harness": "codex"}, machine(opencode=True), SVILUPPO_WORKSPACE)
    assert lane.kind == "acp"
    assert lane.reason == harness_lanes.MISSING
    assert "codex" in harness_lanes.problem(lane)


def test_a_harness_with_no_folder_to_run_in_is_not_applied() -> None:
    lane = harness_lanes.lane_of("sviluppo", {"harness": "opencode"}, EVERYTHING, "")
    assert lane.reason == harness_lanes.NO_WORKSPACE


def test_the_lane_problem_a_person_reads_is_translated() -> None:
    """A typo in the squad names an agent and the key that was typed: it must read Italian here."""
    from nexgen_core import i18n

    i18n.set_language("it")
    try:
        lane = harness_lanes.lane_of("sviluppo", {"harness": "frotz"}, EVERYTHING, SVILUPPO_WORKSPACE)
        assert harness_lanes.problem(lane) == (
            "Agente sviluppo: `harness: frotz` non è claude, codex, opencode, agy o none, "
            "quindi il setup non ha scritto nulla per lui."
        )
    finally:
        i18n.set_language(None)


def test_the_model_is_read_from_both_shapes_a_file_stores() -> None:
    assert harness_lanes.model_of({"model": " anthropic/claude-opus-5-5 "}) == "anthropic/claude-opus-5-5"
    assert harness_lanes.model_of({"model": {"primary": "openai/gpt-6-luna", "fallbacks": []}}) == "openai/gpt-6-luna"
    assert harness_lanes.model_of({"model": {"fallbacks": ["x"]}}) == ""
    assert harness_lanes.model_of({}) == ""


def test_the_lane_list_keeps_the_order_the_squad_declares() -> None:
    squad = {"agents": {"main": {}, "capo": {"harness": "claude"}, "sviluppo": {"harness": "opencode"}}}
    lanes = harness_lanes.lanes(squad, EVERYTHING, {"main": "/w", "capo": "/w-capo", "sviluppo": "/w-sv"})
    assert [lane.agent for lane in lanes] == ["main", "capo", "sviluppo"]
    assert [lane.kind for lane in lanes] == ["provider", "acp", "acp"]


# ------------------------------------------------------- the two blocks


def test_the_acp_binding_is_one_exact_dict() -> None:
    assert binding("sviluppo", "opencode", SVILUPPO_WORKSPACE, TELEGRAM) == SVILUPPO_ACP_BINDING


def test_the_binding_refuses_agy_and_says_what_it_would_cost() -> None:
    with pytest.raises(ValueError, match="agy_acp_server"):
        binding("capo", "agy", "/w", TELEGRAM)
    with pytest.raises(ValueError, match="without an ACP adapter"):
        acp_agent("frotz")


def test_the_harness_to_acp_map_has_three_entries_and_no_agy() -> None:
    assert HARNESS_TO_ACP == {"claude": "claude", "codex": "codex", "opencode": "opencode"}
    assert acp_agent("OpEnCoDe") == "opencode"


def test_the_whole_agent_runtime_block_is_one_exact_dict() -> None:
    assert acp_runtime("claude", "/state/workspace-capo") == {
        "type": "acp",
        "acp": {
            "agent": "claude",
            "backend": "acpx",
            "mode": "persistent",
            "cwd": "/state/workspace-capo",
        },
    }


# ------------------------------------------------- the setup writes both halves


def test_a_declared_harness_gets_the_runtime_block_and_the_binding() -> None:
    """The acceptance case: an opted-in agent ends up with both halves, and a usable bridge."""
    config = proposal()
    squad = {"agents": {"sviluppo": {"harness": "opencode"}}}

    reported = harness_lanes.apply(config, squad, machine(opencode=True))

    assert reported == ()
    assert config["agents"]["entries"]["sviluppo"]["runtime"] == {
        "type": "acp",
        "acp": {"agent": "opencode", "backend": "acpx", "mode": "persistent", "cwd": SVILUPPO_WORKSPACE},
    }
    assert config["bindings"] == [SVILUPPO_ACP_BINDING]
    assert config["acp"] == {"enabled": True, "backend": "acpx", "allowedAgents": ["opencode"]}
    assert config["plugins"]["allow"] == ["acpx"]
    assert config["plugins"]["entries"]["acpx"]["config"]["permissionMode"] == "approve-all"


def test_an_agent_without_the_key_keeps_its_lane_exactly() -> None:
    """The default: the same proposal, untouched, whatever harnesses the machine has."""
    config = proposal()
    before = proposal()

    reported = harness_lanes.apply(
        config, {"agents": {"sviluppo": {"model": "opencode-go/deepseek-v4.1-flash"}}}, EVERYTHING,
    )

    assert reported == ()
    assert config == before
    assert "runtime" not in config["agents"]["entries"]["sviluppo"]
    assert config["bindings"][0] == {"agentId": "sviluppo", "match": dict(TELEGRAM)}


def test_a_harness_agent_with_no_binding_gets_neither_half() -> None:
    """A runtime block with no bound session answers as a plain model: so neither is written."""
    config = proposal(binding_agent="main")
    config["agents"]["entries"]["main"] = {"name": "main", "workspace": "/state/workspace"}
    squad = {"agents": {"sviluppo": {"harness": "opencode"}}}

    reported = harness_lanes.apply(config, squad, machine(opencode=True))

    assert [lane.agent for lane in reported] == ["sviluppo"]
    assert reported[0].reason == harness_lanes.UNROUTED
    assert "runtime" not in config["agents"]["entries"]["sviluppo"]
    assert config["bindings"] == [{"agentId": "main", "match": dict(TELEGRAM)}]
    assert "acp" not in config


def test_agy_declared_as_a_harness_writes_no_acp_block_at_all() -> None:
    """The plugin road is `agy`'s own, chosen by the model ref: this module writes nothing."""
    config = proposal(binding_agent="capo")
    before = proposal(binding_agent="capo")

    reported = harness_lanes.apply(config, {"agents": {"capo": {"harness": "agy"}}}, EVERYTHING)

    assert reported == ()
    assert config == before


def test_the_bridge_allows_a_declared_harness_that_has_no_go_key() -> None:
    """An ACP session runs on the CLI's own login, so the flat key is not a precondition."""
    config = proposal()
    squad = {"agents": {"sviluppo": {"harness": "opencode"}}}

    harness_lanes.apply(config, squad, machine(opencode=True))

    assert config["acp"]["allowedAgents"] == ["opencode"]
    assert acp_allowed_agents(machine(opencode=True)) == []


def test_a_machine_with_no_cli_for_the_declared_harness_is_told_not_written() -> None:
    config = proposal()
    before = proposal()

    reported = harness_lanes.apply(config, {"agents": {"sviluppo": {"harness": "opencode"}}}, machine(claude=True))

    assert [lane.reason for lane in reported] == [harness_lanes.MISSING]
    assert config == before


# ------------------------------------------------------- the merge


def test_an_acp_binding_replaces_the_route_binding_of_the_same_conversation() -> None:
    """Same agent, same match: without this the route binding already in the file wins."""
    current = {"bindings": [{"agentId": "sviluppo", "match": dict(TELEGRAM)}]}
    proposed = {"bindings": [binding("sviluppo", "opencode", SVILUPPO_WORKSPACE, TELEGRAM)]}

    merged = setup_write.merged_document(current, proposed)

    assert merged["bindings"] == [SVILUPPO_ACP_BINDING]


def test_a_second_merge_of_the_same_lane_changes_nothing() -> None:
    """A setup runs every week: the lane must not flip the file on each run."""
    config = proposal()
    harness_lanes.apply(config, {"agents": {"sviluppo": {"harness": "opencode"}}}, machine(opencode=True))
    current = {"bindings": [{"agentId": "sviluppo", "match": dict(TELEGRAM)}]}

    first = setup_write.merged_document(current, config)
    second = setup_write.merged_document(first, config)

    assert second == first
    assert first["bindings"] == [SVILUPPO_ACP_BINDING]


def test_a_route_binding_never_replaces_an_acp_session_already_in_force() -> None:
    current = {"bindings": [dict(SVILUPPO_ACP_BINDING)]}
    proposed = {"bindings": [{"agentId": "sviluppo", "match": dict(TELEGRAM)}]}

    merged = setup_write.merged_document(current, proposed)

    assert merged["bindings"] == [SVILUPPO_ACP_BINDING]


def test_a_whole_agent_runtime_the_pinned_release_ignores_is_removed() -> None:
    current = {"agents": {"entries": {"capo": {
        "model": "anthropic/claude-opus-5-5", "runtime": {"type": "embedded"},
    }}}}
    proposed = {"agents": {"entries": {"capo": {"model": "anthropic/claude-opus-5-5"}}}}

    merged = setup_write.merged_document(current, proposed)

    assert merged["agents"]["entries"]["capo"] == {"model": "anthropic/claude-opus-5-5"}


def test_a_runtime_the_person_wrote_by_hand_stays() -> None:
    mine = {"type": "embedded", "note": "mine"}
    current = {"agents": {"entries": {"capo": {"runtime": dict(mine)}}}}

    merged = setup_write.merged_document(current, {"agents": {"entries": {"capo": {}}}})

    assert merged["agents"]["entries"]["capo"]["runtime"] == mine


def test_the_acp_runtime_this_engine_writes_survives_the_retirement() -> None:
    block = acp_runtime("claude", "/state/workspace-capo")
    current = {"agents": {"entries": {"capo": {"runtime": block}}}}

    merged = setup_write.merged_document(current, {"agents": {"entries": {"capo": {}}}})

    assert merged["agents"]["entries"]["capo"]["runtime"] == block
