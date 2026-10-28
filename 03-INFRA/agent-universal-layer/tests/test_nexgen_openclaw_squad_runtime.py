"""Tests for the squad `runtime` block: chats per agent, fork lifetime, housekeeping."""
from __future__ import annotations

import pytest

from nexgen_core.openclaw.runtime import apply_runtime
from nexgen_core.openclaw.squad import apply_squad


def test_dm_scope_and_fork_lifetime_land_where_openclaw_reads_them():
    config: dict = {}

    apply_runtime(config, {"dm_scope": "main", "archive_forks_after": "24h"})

    assert config["session"]["dmScope"] == "main"
    assert config["session"]["maintenance"]["archiveDashboardAfter"] == "24h"
    # Archiving only reports in warn mode, so the duration implies enforce.
    assert config["session"]["maintenance"]["mode"] == "enforce"


def test_disabling_fork_archiving_leaves_the_mode_alone():
    config: dict = {}

    apply_runtime(config, {"archive_forks_after": False})

    assert config["session"]["maintenance"]["archiveDashboardAfter"] is False
    assert "mode" not in config["session"]["maintenance"]


def test_durations_become_the_units_each_key_expects():
    config: dict = {}

    apply_runtime(
        config,
        {"automation_session_retention": "2h", "mcp_idle_ttl": "5m"},
    )

    assert config["cron"]["sessionRetention"] == "2h"
    assert config["mcp"]["sessionIdleTtlMs"] == 300_000


def test_housekeeping_models_are_written_under_agent_defaults():
    config: dict = {}

    apply_runtime(
        config,
        {
            "subagent_model": "opencode-go/deepseek-v4.1-flash",
            "compaction_model": "opencode-go/deepseek-v4.1-flash",
        },
    )

    defaults = config["agents"]["defaults"]
    assert defaults["subagents"]["model"] == "opencode-go/deepseek-v4.1-flash"
    assert defaults["compaction"]["model"] == "opencode-go/deepseek-v4.1-flash"


def test_a_typo_fails_the_setup_instead_of_writing_a_dead_key():
    with pytest.raises(ValueError, match="sconosciute"):
        apply_runtime({}, {"archive_fork_after": "24h"})


@pytest.mark.parametrize("bad", ["24", "un giorno", "", "0h"])
def test_an_unreadable_duration_is_rejected(bad):
    with pytest.raises(ValueError):
        apply_runtime({}, {"mcp_idle_ttl": bad})


def test_an_unknown_dm_scope_is_rejected():
    with pytest.raises(ValueError, match="dm_scope"):
        apply_runtime({}, {"dm_scope": "per-tavolo"})


def test_apply_squad_carries_the_runtime_block_with_the_roster():
    squad = {
        "default": "main",
        "agents": {"main": {"name": "KURISU", "model": "anthropic/claude-sonnet-5-5"}},
        "runtime": {"dm_scope": "main", "archive_forks_after": "24h"},
    }

    config = apply_squad({}, squad)

    assert config["agents"]["entries"]["main"]["name"] == "KURISU"
    assert config["session"]["dmScope"] == "main"
    assert config["session"]["maintenance"]["archiveDashboardAfter"] == "24h"
