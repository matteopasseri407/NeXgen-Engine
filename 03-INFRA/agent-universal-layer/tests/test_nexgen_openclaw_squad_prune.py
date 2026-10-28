"""Tests for pruning agents removed from the squad: opt-in, never automatic."""
from __future__ import annotations

from nexgen_core.openclaw.squad import apply_squad


def a_squad(**extra):
    squad = {
        "default": "main",
        "agents": {
            "main": {"name": "KURISU", "model": "anthropic/claude-sonnet-5-5"},
            "daru": {"name": "Daru", "model": "anthropic/claude-opus-5-5"},
        },
    }
    squad.update(extra)
    return squad


def a_config_with_a_stale_agent():
    return {"agents": {"entries": {"siti": {"name": "Siti web"}, "main": {}}}}


def test_a_removed_agent_is_pruned_when_the_squad_asks_for_it():
    config = apply_squad(a_config_with_a_stale_agent(), a_squad(prune_agents=True))

    assert "siti" not in config["agents"]["entries"]
    assert set(config["agents"]["entries"]) == {"main", "daru"}


def test_the_engine_stays_additive_without_the_flag():
    config = apply_squad(a_config_with_a_stale_agent(), a_squad())

    assert "siti" in config["agents"]["entries"]


def test_the_flag_without_a_roster_prunes_nothing():
    config = apply_squad({"agents": {"entries": {"siti": {}}}}, {"prune_agents": True})

    assert "siti" in config["agents"]["entries"]
