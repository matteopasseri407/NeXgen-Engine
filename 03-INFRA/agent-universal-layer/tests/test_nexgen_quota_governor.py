"""The fallback policy: what the owner declares, where it lands, and what a person reads.

Every fixture is built under `tmp_path`: a `quota.yaml` with its `failover:`
section, an OpenClaw configuration with the agents the engine creates, and a
plugin folder when the state or the sidecar has to be written. The engine never
touches the home it runs on in these tests, and the policy is asserted from what
`nexgen quota`, `nexgen openclaw setup` and the doctor would actually do with it.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from nexgen_core import i18n, quota, quota_policy, quota_state, quota_watch
from nexgen_core.openclaw import governor, setup_write
from nexgen_core.openclaw.setup_steps import Setup, governor_step
from nexgen_core.report import Severity

#: One frozen moment, computed rather than written: a literal epoch is a long number a
#: leak scan cannot tell from an identifier, and this fixture is about a reading, not a time.
WHEN = "2026-10-23T11:21:43.532Z"
NOW = datetime(2026, 10, 23, 11, 21, 43, tzinfo=timezone.utc).timestamp() + 0.532
RESETS_AT = int(NOW) + 3600

QUOTA_YAML = ("limits:\n  opencode-go:\n    5h: 12.0\n"
              "failover:\n"
              "  chain:\n"
              "    - anthropic/claude-opus-5-5\n"
              "    - openai/gpt-6-luna\n"
              "  agents:\n"
              "    capo:\n"
              "      - openai/gpt-6-luna\n")


@pytest.fixture(autouse=True)
def _english_notes():
    """Pin the source language: the sentences are asserted below."""
    i18n.set_language("en")
    yield
    i18n.set_language(None)


def quota_file(home: Path, text: str = QUOTA_YAML) -> Path:
    """The `quota.yaml` the engine reads: one file for the ceilings and the policy."""
    path = home / "KnowledgeVault" / "03-INFRA" / "agent-universal-layer" / "quota.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def plugin_folder(home: Path) -> Path:
    """The deployed plugin, which is what makes the state and the sidecar readable."""
    folder = home / ".openclaw" / "extensions" / "nexgen"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


class Recorder:
    """A megaphone that remembers instead of telling the person's machine anything."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    def send_alert(self, title: str, message: str, action: str | None = None,
                   alert_key: str | None = None) -> bool:
        self.sent.append({"title": title, "message": message, "action": action, "key": alert_key})
        return True


def agent_config(*agents: str) -> dict:
    """An OpenClaw configuration with the agents a setup would create."""
    return {
        "agents": {
            "ownership": "explicit",
            "defaults": {"heartbeat": {"every": "0m"}},
            "entries": {agent: {"name": agent, "model": f"opencode-go/{agent}-model"} for agent in agents},
        },
    }


# ------------------------------------------------------------------- the policy


def test_a_machine_with_no_file_declares_nothing_and_says_nothing(tmp_path):
    policy, notes = quota_policy.load(tmp_path)

    assert policy.declared is False
    assert policy.chain == () and policy.agents == {}
    assert notes == []


def test_the_shared_chain_and_the_agent_one_are_read_side_by_side(tmp_path):
    quota_file(tmp_path)

    policy, notes = quota_policy.load(tmp_path)

    assert notes == []
    assert policy.chain == ("anthropic/claude-opus-5-5", "openai/gpt-6-luna")
    assert policy.for_agent("capo") == ("openai/gpt-6-luna",)
    assert policy.for_agent("main") == policy.chain


def test_a_ref_that_is_not_provider_slash_model_is_named_and_dropped(tmp_path):
    quota_file(tmp_path, "failover:\n  chain:\n    - claude-opus\n    - 'anthropic/claude opus'\n    - openai/gpt-6-luna\n")

    policy, notes = quota_policy.load(tmp_path)

    assert policy.chain == ("openai/gpt-6-luna",)
    assert sum("is not written as 'provider/model'" in note for note in notes) == 2


def test_the_line_between_being_fine_and_being_spent_is_the_owners(tmp_path):
    quota_file(tmp_path, "failover:\n  block_at: 90\n  chain:\n    - openai/gpt-6-luna\n")

    policy, _notes = quota_policy.load(tmp_path)

    assert policy.block_at == 90.0


def test_a_line_that_is_not_a_percentage_falls_back_to_the_shipped_one(tmp_path):
    quota_file(tmp_path, "failover:\n  block_at: 'quasi'\n  chain:\n    - openai/gpt-6-luna\n")

    policy, notes = quota_policy.load(tmp_path)

    assert policy.block_at == quota_policy.DEFAULT_BLOCK_AT
    assert any("not a percentage between 0 and 100" in note for note in notes)


def test_the_reading_age_is_declared_in_minutes_and_stored_in_seconds(tmp_path):
    quota_file(tmp_path, "failover:\n  state_max_age_minutes: 45\n  chain:\n    - openai/gpt-6-luna\n")

    policy, _notes = quota_policy.load(tmp_path)

    assert policy.state_max_age_seconds == 45 * 60


def test_the_shipped_line_is_the_urgent_alert_band():
    """One owner for what "high" means: the plan that raises the loudest alert is the one about to stop."""
    assert quota_policy.DEFAULT_BLOCK_AT == quota.ALERT_AT[-1]


def test_an_unreadable_policy_costs_the_policy_and_never_the_command(tmp_path):
    path = quota_file(tmp_path, "failover: [not: a: map\n")

    policy, notes = quota_policy.load(tmp_path)

    assert policy.declared is False
    assert len(notes) == 1 and "cannot be read" in notes[0]
    assert path.is_file()


def test_the_policy_is_the_file_the_ceilings_already_live_in(tmp_path):
    quota_file(tmp_path)

    assert quota_policy.path(tmp_path) == quota.limits_path(tmp_path)
    limits, notes = quota.load_limits(quota.limits_path(tmp_path))
    assert limits == {"opencode-go": {"5h": 12.0}} and notes == []


def test_a_row_hands_over_to_the_first_model_that_runs_elsewhere():
    chain = ("anthropic/claude-opus-5-5", "opencode-go/deepseek-v4-pro")

    assert quota_policy.handover_to(chain, "opencode-go") == "anthropic/claude-opus-5-5"
    assert quota_policy.handover_to(chain, "anthropic") == "opencode-go/deepseek-v4-pro"
    assert quota_policy.handover_to(("opencode-go/deepseek-v4-pro",), "opencode-go") == "opencode-go/deepseek-v4-pro"
    assert quota_policy.handover_to((), "openai") == ""


# -------------------------------------------------------------------- the state


def a_row(provider_id: str = "opencode-go", percent: float | None = 97.0, window: str = "5h") -> quota.QuotaRow:
    return quota.QuotaRow(provider="OpenCode Go", provider_id=provider_id, window=window,
                          unit="usd", used=11.6, percent=percent, state="warn")


def test_the_state_keeps_the_worst_window_of_each_provider():
    rows = [a_row("opencode-go", 20.0, "5h"), a_row("opencode-go", 97.0, "7d"),
            a_row("openai", 40.0, "30d"), a_row("", 99.0, "5h")]

    payload = quota_state.payload(rows, now=1000.0)

    assert payload["written_at"] == 1000.0
    assert payload["providers"] == {"opencode-go": {"percent": 97.0, "window": "7d"},
                                    "openai": {"percent": 40.0, "window": "30d"}}


def test_a_provider_with_no_percentage_is_absent_rather_than_zero():
    assert quota_state.payload([a_row("opencode-go", None)], now=1.0)["providers"] == {}


def test_nothing_is_written_where_no_plugin_was_ever_deposited(tmp_path):
    assert quota_state.write(tmp_path, [a_row()], now=1000.0) == "skipped"
    assert not (tmp_path / ".openclaw").exists()


def test_the_state_lands_beside_the_plugin(tmp_path):
    folder = plugin_folder(tmp_path)

    assert quota_state.write(tmp_path, [a_row()], now=1000.0) == "written"
    written = json.loads((folder / "nexgen-quota-state.json").read_text(encoding="utf-8"))
    assert written["providers"]["opencode-go"] == {"percent": 97.0, "window": "5h"}
    assert written["written_at"] == 1000.0


def test_a_state_that_cannot_be_written_costs_the_file_and_not_the_beat(tmp_path):
    folder = plugin_folder(tmp_path)
    (folder / "nexgen-quota-state.json").mkdir()  # a directory where a file belongs

    assert quota_state.write(tmp_path, [a_row()], now=1000.0) == "failed"


def test_the_beat_leaves_the_readings_it_just_judged_beside_the_plugin(tmp_path):
    plugin_folder(tmp_path)
    codex = tmp_path / ".codex" / "sessions" / "2026" / "10" / "23"
    codex.mkdir(parents=True)
    (codex / "rollout-2026-10-23.jsonl").write_text(json.dumps({
        "timestamp": WHEN,
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "rate_limits": {
                "limit_id": "codex",
                "primary": {"used_percent": 96.0, "window_minutes": 43200,
                            "resets_at": RESETS_AT},
                "secondary": None,
                "credits": {"has_credits": False, "balance": None},
                "plan_type": "free",
            },
        },
    }) + "\n", encoding="utf-8")

    answer = quota_watch.check_and_alert(home=tmp_path, now=NOW, megaphone=Recorder())

    assert answer["state"] == "written"
    written = json.loads((tmp_path / ".openclaw" / "extensions" / "nexgen" / "nexgen-quota-state.json")
                         .read_text(encoding="utf-8"))
    assert written["providers"]["openai"]["percent"] == 96.0


# ------------------------------------------------------------- the configuration


def a_policy(**overrides) -> quota_policy.Failover:
    base = {"chain": ("anthropic/claude-opus-5-5",), "agents": {"capo": ("openai/gpt-6-luna",)}}
    return quota_policy.Failover(**{**base, **overrides})


def test_an_empty_policy_changes_nothing_in_the_proposal():
    config = agent_config("main", "capo")

    assert governor.with_chains(config, quota_policy.Failover()) == config


def test_the_proposal_carries_the_chain_in_the_shape_openclaw_documents():
    config = governor.with_chains(agent_config("main", "capo"), a_policy())

    assert config["agents"]["entries"]["main"]["model"] == {
        "primary": "opencode-go/main-model", "fallbacks": ["anthropic/claude-opus-5-5"],
    }
    assert config["agents"]["entries"]["capo"]["model"]["fallbacks"] == ["openai/gpt-6-luna"]


def test_the_sidecar_section_names_only_the_agents_the_setup_creates():
    config = governor.with_chains(agent_config("main"), a_policy())

    section = governor.governor_section(a_policy(), config)

    assert section == {
        "block_at": 95.0,
        "state_max_age_seconds": 10800,
        "agents": {"main": ["anthropic/claude-opus-5-5"]},
    }


def test_no_section_is_written_when_the_policy_names_no_created_agent():
    config = agent_config("main")

    assert governor.governor_section(quota_policy.Failover(agents={"triage": ("openai/gpt-6-luna",)}), config) is None


def test_a_chain_written_by_hand_survives_and_comes_first():
    current = {"agents": {"entries": {"main": {"model": {"primary": "opencode-go/main-model",
                                                          "fallbacks": ["ollama/qwen3:8b"]}}}}}
    proposed = {"agents": {"entries": {"main": {"model": {"primary": "opencode-go/main-model",
                                                           "fallbacks": ["anthropic/claude-opus-5-5"]}}}}}

    merged = setup_write.merged_document(current, proposed)

    assert merged["agents"]["entries"]["main"]["model"]["fallbacks"] == [
        "ollama/qwen3:8b", "anthropic/claude-opus-5-5",
    ]


def test_a_chain_written_by_hand_survives_a_proposal_that_names_none():
    """Without a policy the engine proposes a plain string; the person's chain must not vanish."""
    current = {"agents": {"entries": {"main": {"model": {"primary": "opencode-go/main-model",
                                                          "fallbacks": ["ollama/qwen3:8b"]}}}}}
    proposed = {"agents": {"entries": {"main": {"model": "opencode-go/main-model"}}}}

    merged = setup_write.merged_document(current, proposed)

    assert merged["agents"]["entries"]["main"]["model"] == {
        "primary": "opencode-go/main-model", "fallbacks": ["ollama/qwen3:8b"],
    }


def test_the_sidecar_section_is_replaced_whole_and_removed_with_the_policy(tmp_path):
    from nexgen_core.runtimes.openclaw import OpenClawRuntime

    folder = plugin_folder(tmp_path)
    runtime = OpenClawRuntime()

    assert runtime.replace_governor(tmp_path, {"agents": {"main": ["a/b"], "capo": ["c/d"]}}) is True
    first = json.loads((folder / "nexgen.config.json").read_text(encoding="utf-8"))
    assert first["governor"]["agents"] == {"main": ["a/b"], "capo": ["c/d"]}

    # A chain the owner deleted from the policy must stop being offered, not linger.
    assert runtime.replace_governor(tmp_path, {"agents": {"main": ["a/b"]}}) is True
    second = json.loads((folder / "nexgen.config.json").read_text(encoding="utf-8"))
    assert second["governor"]["agents"] == {"main": ["a/b"]}

    assert runtime.replace_governor(tmp_path, None) is True
    assert "governor" not in json.loads((folder / "nexgen.config.json").read_text(encoding="utf-8"))


def test_the_sidecar_is_not_written_where_no_plugin_was_deposited(tmp_path):
    from nexgen_core.runtimes.openclaw import OpenClawRuntime

    assert OpenClawRuntime().replace_governor(tmp_path, {"agents": {"main": ["a/b"]}}) is False
    assert not (tmp_path / ".openclaw").exists()


def a_setup(tmp_path: Path, out: list[str]) -> Setup:
    """The pieces of a setup run that the governor step touches, and nothing else."""
    return Setup(
        home=tmp_path, env={}, config_path=tmp_path / ".openclaw" / "openclaw.json",
        engine_root=None, vault_data=tmp_path / "KnowledgeVault", pinned=None, dry_run=False,
        out=out.append, which=lambda _name: None, run=None, service=None, stamp="2026-01-01",
    )


def test_the_setup_step_writes_the_chains_beside_the_plugin_and_says_how_many(tmp_path):
    plugin_folder(tmp_path)
    printed: list[str] = []
    config = governor.with_chains(agent_config("main", "capo"), a_policy())

    governor_step(a_setup(tmp_path, printed), type("P", (), {"failover": a_policy(), "config": config})())

    written = json.loads((tmp_path / ".openclaw" / "extensions" / "nexgen" / "nexgen.config.json")
                         .read_text(encoding="utf-8"))
    assert set(written["governor"]["agents"]) == {"main", "capo"}
    assert "reach 2 agents" in "\n".join(printed)


def test_the_setup_step_writes_nothing_on_an_empty_policy(tmp_path):
    plugin_folder(tmp_path)
    printed: list[str] = []
    config = governor.with_chains(agent_config("main"), quota_policy.Failover())

    governor_step(a_setup(tmp_path, printed),
                  type("P", (), {"failover": quota_policy.Failover(), "config": config})())

    assert "no chain was written" in "\n".join(printed)
    assert not (tmp_path / ".openclaw" / "extensions" / "nexgen" / "nexgen.config.json").exists()


# ------------------------------------------------------------------------ doctor


def test_the_doctor_line_counts_the_agents_that_have_a_chain():
    config = governor.with_chains(agent_config("main", "capo"), a_policy())

    outcome = governor.check_fallbacks(config, a_policy())

    assert outcome.id == "openclaw.fallbacks"
    assert outcome.severity == Severity.OK
    assert outcome.message == "The fallback policy is declared: every one of the 2 agents has a chain."


def test_the_doctor_line_names_an_agent_the_configuration_forgot():
    config = agent_config("main", "triage")
    policy = quota_policy.Failover(agents={"triage": ("openai/gpt-6-luna",)})

    outcome = governor.check_fallbacks(config, policy)

    assert outcome.severity == Severity.WARN
    assert "triage" in outcome.message
    assert outcome.action and "nexgen openclaw setup" in outcome.action


def test_a_machine_that_declared_nothing_says_so_instead_of_failing():
    outcome = governor.check_fallbacks(agent_config("main", "capo"), quota_policy.Failover())

    assert outcome.severity == Severity.OK
    assert outcome.message == "No fallback policy is declared, and no OpenClaw agent has a fallback chain."


def test_a_chain_written_by_hand_is_counted_even_with_no_policy():
    config = {"agents": {"entries": {
        "main": {"model": {"primary": "opencode-go/x", "fallbacks": ["ollama/qwen3:8b"]}},
        "capo": {"model": "opencode-go/y"},
    }}}

    outcome = governor.check_fallbacks(config, quota_policy.Failover())

    assert outcome.message == ("No fallback policy is declared: 1 agents have a chain of their own, "
                               "1 have none (capo).")


def test_an_agent_that_inherits_the_shared_chain_counts_as_covered():
    """An agent with no model at all inherits `agents.defaults.model.fallbacks`."""
    config = {
        "agents": {
            "defaults": {"model": {"fallbacks": ["openai/gpt-6-luna"]}},
            "entries": {"main": {"name": "main"}},
        },
    }

    outcome = governor.check_fallbacks(config, quota_policy.Failover())

    assert outcome.message == "No fallback policy is declared; the 1 agents have a chain of their own."


def test_the_doctor_runs_the_line_on_a_machine_with_a_configuration(tmp_path):
    from nexgen_core.openclaw.detect import Detection, Installation, LocalModels, Login
    from nexgen_core.openclaw.doctor import collect

    quota_file(tmp_path)
    config_path = tmp_path / ".openclaw" / "openclaw.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps(governor.with_chains(agent_config("main"), a_policy())), encoding="utf-8")
    snapshot = Detection(
        openclaw=Installation(installed=True, version="2026.10.6", config_path=str(config_path),
                              config_present=True),
        claude=Login(binary=False), codex=Login(binary=False), opencode_go=True, local=LocalModels(), n8n=False,
    )

    outcomes = collect(detected=snapshot, home=tmp_path, env={}, vault_data=tmp_path / "KnowledgeVault",
                       platform="linux", connect=lambda *_a, **_k: True)

    line = next(outcome for outcome in outcomes if outcome.id == "openclaw.fallbacks")
    assert line.severity == Severity.OK
    assert "every one of the 1 agents has a chain" in line.message


# ------------------------------------------------------------------------ report


def codex_reading(home: Path, percent: float) -> None:
    """A Codex session carrying one rate-limit line, where Codex writes it."""
    folder = home / ".codex" / "sessions" / "2026" / "10" / "23"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "rollout-2026-10-23.jsonl").write_text(json.dumps({
        "timestamp": WHEN,
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "rate_limits": {
                "limit_id": "codex",
                "primary": {"used_percent": percent, "window_minutes": 43200, "resets_at": RESETS_AT},
                "secondary": None,
                "credits": {"has_credits": False, "balance": None},
                "plan_type": "free",
            },
        },
    }) + "\n", encoding="utf-8")


def test_a_row_says_what_the_plan_hands_over_to(tmp_path):
    codex_reading(tmp_path, 59.0)
    quota_file(tmp_path)

    report = quota.build_report(home=tmp_path, now=NOW)

    row = next(row for row in report.rows if row.provider_id == "openai")
    assert row.fallback == "anthropic/claude-opus-5-5"
    assert report.lines[0].endswith(", then anthropic/claude-opus-5-5")


def test_a_machine_with_no_policy_shows_no_handover_and_invents_none(tmp_path):
    codex_reading(tmp_path, 59.0)

    report = quota.build_report(home=tmp_path, now=NOW)

    row = next(row for row in report.rows if row.provider_id == "openai")
    assert row.fallback == ""
    assert "then " not in report.lines[0]


def test_the_json_carries_the_provider_id_and_the_handover(tmp_path):
    codex_reading(tmp_path, 59.0)
    quota_file(tmp_path)

    data = quota.build_report(home=tmp_path, now=NOW).to_dict()

    codex_row = next(row for row in data["rows"] if row["provider_id"] == "openai")
    assert codex_row["fallback"] == "anthropic/claude-opus-5-5"
    assert data["lines"][0].endswith(codex_row["fallback"])
