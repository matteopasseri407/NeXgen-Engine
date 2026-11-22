"""The squad's subagent policy: who may spawn whom, on which model, and the ceilings.

Expected shapes are the ones the installed OpenClaw documents for
`agents.entries.<id>.subagents.allowAgents`, `agents.entries.<id>.subagents.model`
and `agents.defaults.subagents` (`docs/tools/subagents/nesting.md`,
`docs/gateway/config-agents/entries-and-multi-agent.md`). The ceil values are the
squad's decision, not OpenClaw's defaults: depth 2, six children, four concurrent
runs, 900 seconds.
"""
from __future__ import annotations

import pytest

from nexgen_core.openclaw import subagents_policy
from nexgen_core.openclaw.squad import apply_squad

KURISU_MODEL = "opencode-go/deepseek-v4.1-flash"


def _squad(**main_extra) -> dict:
    """The shape of the real squad, cut to the three agents a test needs."""
    return {
        "default": "main",
        "agents": {
            "main": {"name": "KURISU", "model": KURISU_MODEL, **main_extra},
            "posta": {"name": "Sheska", "model": KURISU_MODEL},
            "capo": {"name": "Daru", "model": "anthropic/claude-opus-5-5"},
        },
    }


def test_a_declared_block_lands_where_openclaw_reads_it():
    config: dict = {}
    squad = _squad(subagents={"allow": ["posta", "capo"], "model": KURISU_MODEL})

    subagents_policy.apply(config, squad)

    assert config["agents"]["entries"]["main"]["subagents"] == {
        "allowAgents": ["posta", "capo"],
        "model": KURISU_MODEL,
    }


def test_an_agent_without_the_block_gains_no_key():
    config: dict = {}
    squad = _squad()

    subagents_policy.apply(config, squad)

    assert "entries" not in config["agents"]


def test_an_id_the_squad_does_not_declare_is_refused_with_the_name():
    squad = _squad(subagents={"allow": ["posta", "sviluppo"]})

    with pytest.raises(ValueError) as refused:
        subagents_policy.apply({}, squad)

    message = str(refused.value)
    assert "sviluppo" in message
    assert "main" in message


def test_a_wildcard_is_refused_because_it_is_not_an_agent_of_the_squad():
    # `["*"]` is legal in OpenClaw and is exactly what this squad does not want:
    # the allowlist names agents the file declares, never "whichever exists later".
    squad = _squad(subagents={"allow": ["*"]})

    with pytest.raises(ValueError, match="posta"):
        subagents_policy.apply({}, squad)


def test_the_same_id_twice_is_written_once_in_the_declared_order():
    squad = _squad(subagents={"allow": ["capo", "posta", "capo"]})

    config: dict = {}
    subagents_policy.apply(config, squad)

    assert config["agents"]["entries"]["main"]["subagents"]["allowAgents"] == ["capo", "posta"]


def test_an_empty_allow_list_is_written_as_the_denial_it_is():
    squad = _squad(subagents={"allow": []})

    config: dict = {}
    subagents_policy.apply(config, squad)

    assert config["agents"]["entries"]["main"]["subagents"]["allowAgents"] == []


def test_an_existing_block_keeps_every_key_the_squad_does_not_set():
    config = {"agents": {"entries": {"main": {"subagents": {"requireAgentId": True}}}}}
    squad = _squad(subagents={"allow": ["posta"], "model": KURISU_MODEL})

    subagents_policy.apply(config, squad)

    assert config["agents"]["entries"]["main"]["subagents"] == {
        "requireAgentId": True,
        "allowAgents": ["posta"],
        "model": KURISU_MODEL,
    }


def test_an_unknown_key_inside_an_agent_block_is_named_not_guessed():
    # `allowAgents` is the config's spelling: in squad.yaml the key is `allow`.
    squad = _squad(subagents={"allowAgents": ["posta"]})

    with pytest.raises(ValueError, match="allowAgents"):
        subagents_policy.apply({}, squad)


def test_a_block_that_is_not_a_mapping_is_refused_with_the_agent_name():
    squad = _squad(subagents=["posta"])

    with pytest.raises(ValueError, match="main"):
        subagents_policy.apply({}, squad)


def test_empty_strings_and_non_ids_are_refused_not_dropped():
    squad = _squad(subagents={"allow": ["posta", "  ", 3]})

    with pytest.raises(ValueError, match="allow"):
        subagents_policy.apply({}, squad)


def test_an_empty_model_is_refused():
    squad = _squad(subagents={"allow": ["posta"], "model": "  "})

    with pytest.raises(ValueError, match="model"):
        subagents_policy.apply({}, squad)


def test_the_safe_ceilings_are_written_even_when_the_squad_says_nothing():
    config: dict = {}

    subagents_policy.apply(config, {})

    assert config["agents"]["defaults"]["subagents"] == {
        "maxSpawnDepth": 2,
        "maxChildrenPerAgent": 6,
        "maxConcurrent": 4,
        "runTimeoutSeconds": 900,
    }


def test_the_squad_may_raise_or_lower_a_ceiling():
    squad = {"subagents": {"maxSpawnDepth": 3, "runTimeoutSeconds": 600}}

    config: dict = {}
    subagents_policy.apply(config, squad)

    assert config["agents"]["defaults"]["subagents"] == {
        "maxSpawnDepth": 3,
        "maxChildrenPerAgent": 6,
        "maxConcurrent": 4,
        "runTimeoutSeconds": 600,
    }


def test_a_timeout_of_zero_is_refused_because_it_means_no_timeout():
    with pytest.raises(ValueError, match="runTimeoutSeconds") as refused:
        subagents_policy.apply({}, {"subagents": {"runTimeoutSeconds": 0}})

    assert "timeout" in str(refused.value)


@pytest.mark.parametrize(
    "ceiling, value",
    [
        ("maxSpawnDepth", 0),
        ("maxSpawnDepth", 6),
        ("maxChildrenPerAgent", 0),
        ("maxChildrenPerAgent", 21),
        ("maxConcurrent", 0),
        ("runTimeoutSeconds", -1),
    ],
)
def test_a_ceiling_outside_the_documented_range_is_refused(ceiling, value):
    with pytest.raises(ValueError, match=ceiling):
        subagents_policy.apply({}, {"subagents": {ceiling: value}})


@pytest.mark.parametrize("value", [True, "4", 4.5, None])
def test_a_ceiling_that_is_not_a_whole_number_is_refused(value):
    with pytest.raises(ValueError, match="maxConcurrent"):
        subagents_policy.apply({}, {"subagents": {"maxConcurrent": value}})


def test_an_unknown_ceiling_is_named_not_ignored():
    with pytest.raises(ValueError, match="maxDepth"):
        subagents_policy.apply({}, {"subagents": {"maxDepth": 2}})


def test_the_ceilings_block_that_is_not_a_mapping_is_refused():
    with pytest.raises(ValueError, match="subagents"):
        subagents_policy.apply({}, {"subagents": 2})


def test_the_housekeeping_model_from_the_runtime_block_survives_the_ceilings():
    config = {"agents": {"defaults": {"subagents": {"model": KURISU_MODEL}}}}

    subagents_policy.apply(config, {})

    assert config["agents"]["defaults"]["subagents"]["model"] == KURISU_MODEL
    assert config["agents"]["defaults"]["subagents"]["maxSpawnDepth"] == 2


def test_a_ceiling_the_config_carries_is_brought_back_to_the_squad_value():
    # The squad owns these four: an older hand edit does not outlive a setup.
    config = {"agents": {"defaults": {"subagents": {"maxSpawnDepth": 5, "runTimeoutSeconds": 0}}}}

    subagents_policy.apply(config, {})

    assert config["agents"]["defaults"]["subagents"]["maxSpawnDepth"] == 2
    assert config["agents"]["defaults"]["subagents"]["runTimeoutSeconds"] == 900


def test_apply_squad_carries_both_blocks_with_the_roster():
    squad = {
        **_squad(subagents={"allow": ["posta", "capo"], "model": KURISU_MODEL}),
        "subagents": {"maxConcurrent": 2},
    }

    config = apply_squad({}, squad)

    assert config["agents"]["entries"]["main"]["name"] == "KURISU"
    assert config["agents"]["entries"]["main"]["subagents"]["allowAgents"] == ["posta", "capo"]
    assert config["agents"]["defaults"]["subagents"] == {
        "maxSpawnDepth": 2,
        "maxChildrenPerAgent": 6,
        "maxConcurrent": 2,
        "runTimeoutSeconds": 900,
    }


def test_an_invalid_id_fails_the_setup_through_apply_squad():
    squad = _squad(subagents={"allow": ["inesistente"]})

    with pytest.raises(ValueError, match="inesistente"):
        apply_squad({}, squad)
