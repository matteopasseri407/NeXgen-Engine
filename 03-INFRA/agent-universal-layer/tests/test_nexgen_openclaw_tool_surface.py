"""`tool_surface: lean`: which tools a CLI-backend agent keeps, and where they land.

Every expected value below is a literal from the specification (`docs/squad.md`, the
option table) or from the measurement in `docs/proposals/daru-burn.md` (L1), never a
value recomputed the way the code computes it.
"""
from __future__ import annotations

from nexgen_core.openclaw import tool_surface
from nexgen_core.openclaw.squad import apply_squad

CLAUDE_REF = "anthropic/claude-opus-5-5"


def a_config() -> dict:
    """An OpenClaw configuration with the two agents a lean squad would declare."""
    return {
        "agents": {"entries": {"main": {"model": "opencode-go/deepseek-v4.1-flash"},
                               "capo": {"model": CLAUDE_REF}}},
        "tools": {"byProvider": {}},
    }


def a_squad(**capo: object) -> dict:
    """A squad whose `capo` declares the lean surface, plus whatever the test adds."""
    return {"agents": {"capo": {"name": "capo", "model": CLAUDE_REF,
                                "tool_surface": "lean", **capo}}}


def test_a_lean_agent_gets_the_minimal_profile_and_its_own_allowlist():
    config = a_config()

    tool_surface.apply(config, a_squad())

    entry = config["tools"]["byProvider"][CLAUDE_REF]
    assert entry["profile"] == "minimal"
    # The tools a Daru turn was measured using, and the connector road.
    for kept in ("exec", "read", "edit", "apply_patch", "sessions_send", "memory_search", "message"):
        assert kept in entry["alsoAllow"]
    # The schemas that are 42% of the prompt and are not on that road.
    for dropped in ("browser", "canvas", "plugins", "tts", "talk_voice", "nodes"):
        assert dropped not in entry["alsoAllow"]


def test_the_connector_road_survives_the_lean_surface():
    config = a_config()

    tool_surface.apply(config, a_squad())

    also_allow = config["tools"]["byProvider"][CLAUDE_REF]["alsoAllow"]
    assert "lazy-mcp__lazy_call" in also_allow
    assert "lazy-mcp__lazy_list" in also_allow


def test_the_supervisor_keeps_the_tools_that_drive_the_supervision():
    """The supervision tools are `optional`: a lean surface without them removes the feature."""
    config = a_config()

    tool_surface.apply(config, a_squad())

    also_allow = config["tools"]["byProvider"][CLAUDE_REF]["alsoAllow"]
    assert "nexgen_sup_start" in also_allow
    assert "nexgen_sup_publish" in also_allow


def test_an_agent_that_does_not_supervise_does_not_receive_the_supervision_tools():
    config = {"agents": {"entries": {"capo": {"model": CLAUDE_REF}}}, "tools": {"byProvider": {}}}
    squad = {"agents": {"sviluppo": {"model": "opencode-go/deepseek-v4.1-flash", "tool_surface": "lean"}}}

    tool_surface.apply(config, squad)

    assert "nexgen_sup_start" not in config["tools"]["byProvider"]["opencode-go/deepseek-v4.1-flash"]["alsoAllow"]


def test_an_agent_without_the_key_is_left_exactly_as_it_is():
    config = a_config()
    squad = {"agents": {"capo": {"name": "capo", "model": CLAUDE_REF}}}

    tool_surface.apply(config, squad)

    assert config["tools"]["byProvider"] == {}


def test_a_value_that_is_not_lean_is_not_guessed():
    config = a_config()
    squad = {"agents": {"capo": {"model": CLAUDE_REF, "tool_surface": "lean-ish"}}}

    tool_surface.apply(config, squad)

    assert config["tools"]["byProvider"] == {}


def test_an_agent_with_no_model_of_its_own_gets_no_surface():
    """Inheriting a model would write a surface for a model the agent never named."""
    config = a_config()
    squad = {"agents": {"capo": {"name": "capo", "tool_surface": "lean"}}}

    tool_surface.apply(config, squad)

    assert config["tools"]["byProvider"] == {}


def test_the_cage_around_a_local_model_is_never_replaced():
    """A local model's `profile: messaging` plus its deny is what stops it writing."""
    config = {"agents": {"entries": {}}, "tools": {"byProvider": {
        "ollama/qwen3:8b": {"profile": "messaging", "deny": ["write", "edit"]}}}}
    squad = {"agents": {"main": {"model": "ollama/qwen3:8b", "tool_surface": "lean"}}}

    tool_surface.apply(config, squad)

    assert config["tools"]["byProvider"]["ollama/qwen3:8b"] == {
        "profile": "messaging", "deny": ["write", "edit"],
    }


def test_an_allowlist_a_person_wrote_is_extended_and_never_replaced():
    config = {"agents": {"entries": {}}, "tools": {"byProvider": {
        CLAUDE_REF: {"profile": "minimal", "alsoAllow": ["goal_create"]}}}}

    tool_surface.apply(config, a_squad())

    also_allow = config["tools"]["byProvider"][CLAUDE_REF]["alsoAllow"]
    assert "goal_create" in also_allow
    assert "exec" in also_allow


def test_the_engine_never_writes_allow_beside_alsoallow():
    """OpenClaw refuses the pair in one scope; the lean entry is `alsoAllow` only."""
    config = a_config()

    tool_surface.apply(config, a_squad())

    assert "allow" not in config["tools"]["byProvider"][CLAUDE_REF]


def test_the_squad_writes_the_surface_and_leaves_the_other_agents_alone():
    config = {"agents": {"entries": {}}, "tools": {"byProvider": {}}}

    applied = apply_squad(config, a_squad())

    assert applied["tools"]["byProvider"][CLAUDE_REF]["profile"] == "minimal"
    assert applied["agents"]["entries"]["capo"]["name"] == "capo"
