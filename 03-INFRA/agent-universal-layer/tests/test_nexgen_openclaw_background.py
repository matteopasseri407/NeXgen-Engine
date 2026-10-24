"""The unattended jobs, and the model they wake.

The rule is one sentence: when the agents' default model is a local one and some
agent already runs a cloud model, dreaming, compaction and active memory use that
cloud model instead, and none of them may take away a model the person chose.
The tests below are that sentence, one clause at a time, plus the merge that has
to keep a person's own allowlist when the engine adds its model to it.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import i18n  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw import background  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.detect import Detection, Installation, LocalModelInfo, LocalModels, Login  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.profile import build_config  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.setup_write import merged_document  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.report import Severity  # noqa: E402 - sys.path shim for cloned checkout

GO = "opencode-go/deepseek-v4.1-flash"
LOCAL = "ollama/qwen3:8b"


@pytest.fixture(autouse=True)
def _english():
    i18n.set_language("en")
    yield
    i18n.set_language(None)


def machine(*, go: bool = True, ollama: bool = True) -> Detection:
    """A machine whose assistant runs the Go key, or a local model when there is no key."""
    return Detection(
        openclaw=Installation(installed=True, version="2026.10.6", config_path=str(Path("/state/openclaw.json"))),
        claude=Login(binary=False),
        codex=Login(binary=False),
        opencode_go=go,
        local=LocalModels(ollama=(LocalModelInfo(name="qwen3:8b", context_window=8192),) if ollama else ()),
        n8n=False,
    )


def a_local_default(**extra) -> dict:
    """The file in force on a machine that chose a local model for its assistant."""
    return {
        "agents": {
            "defaults": {"model": {"primary": LOCAL}, **extra.pop("defaults", {})},
            "entries": {"main": {"model": LOCAL, "workspace": "/w"}, **extra.pop("entries", {})},
        },
        **extra,
    }


def test_the_unattended_jobs_move_to_the_cloud_model_an_agent_already_uses():
    current = a_local_default()
    config = build_config(machine(), current=current)

    memory = config["plugins"]["entries"]["memory-core"]
    assert memory["config"]["dreaming"]["model"] == GO
    assert memory["subagent"] == {"allowModelOverride": True, "allowedModels": [GO]}
    compaction = config["agents"]["defaults"]["compaction"]
    assert compaction["model"] == GO
    assert compaction["memoryFlush"] == {"model": GO}


def test_a_cloud_default_leaves_every_key_alone():
    current = {"agents": {"defaults": {"model": {"primary": GO}}, "entries": {"main": {"model": GO}}}}
    config = build_config(machine(), current=current)

    assert "memory-core" not in config["plugins"]["entries"]
    assert "compaction" not in config["agents"]["defaults"]
    assert "active-memory" not in config["plugins"]["entries"]


def test_a_machine_with_no_cloud_model_writes_nothing():
    current = a_local_default(entries={"main": {"model": LOCAL}})
    config = build_config(machine(go=False), current=current)

    assert "memory-core" not in config["plugins"]["entries"]
    assert "compaction" not in config["agents"]["defaults"]


def test_the_model_the_person_chose_for_dreaming_is_not_replaced():
    current = a_local_default()
    current["plugins"] = {"entries": {"memory-core": {"config": {"dreaming": {"model": "anthropic/claude-opus-5-5"}}}}}
    config = build_config(machine(), current=current)

    memory = config["plugins"]["entries"].get("memory-core", {})
    assert "dreaming" not in memory.get("config", {})


def test_an_explicit_no_on_the_override_gate_is_respected():
    """Without the gate OpenClaw refuses the model, so nothing is proposed for that plugin."""
    current = a_local_default()
    current["plugins"] = {"entries": {"memory-core": {"subagent": {"allowModelOverride": False}}}}
    config = build_config(machine(), current=current)

    assert "memory-core" not in config["plugins"]["entries"]
    # Compaction needs no gate: the person's "no" is about subagent overrides only.
    assert config["agents"]["defaults"]["compaction"]["model"] == GO


def test_active_memory_is_written_only_for_a_plugin_this_machine_runs():
    off = a_local_default()
    off["plugins"] = {"allow": [], "entries": {"active-memory": {"enabled": False}}}
    assert "active-memory" not in build_config(machine(), current=off)["plugins"]["entries"]

    on = a_local_default()
    on["plugins"] = {"allow": ["active-memory"], "entries": {"active-memory": {}}}
    active = build_config(machine(), current=on)["plugins"]["entries"]["active-memory"]
    assert active["config"]["model"] == GO
    assert active["subagent"]["allowModelOverride"] is True


def test_the_model_the_person_chose_for_active_memory_is_not_replaced():
    current = a_local_default()
    current["plugins"] = {
        "allow": ["active-memory"],
        "entries": {"active-memory": {"config": {"model": "openai/gpt-6-luna"}}},
    }
    proposed = build_config(machine(), current=current)
    assert "active-memory" not in proposed["plugins"]["entries"]
    merged = merged_document(current, proposed)
    assert merged["plugins"]["entries"]["active-memory"]["config"] == {"model": "openai/gpt-6-luna"}


def test_a_compaction_model_already_chosen_is_never_overwritten():
    current = a_local_default(defaults={"compaction": {"model": "anthropic/claude-opus-5-5"}})
    compaction = build_config(machine(), current=current)["agents"]["defaults"]["compaction"]
    assert compaction == {"memoryFlush": {"model": GO}}


def test_the_persons_allowlist_survives_the_engines_model(tmp_path):
    """Merging is additive here too: the engine's model joins the list, it does not replace it."""
    current = {
        "plugins": {
            "entries": {
                "memory-core": {"subagent": {"allowModelOverride": True, "allowedModels": ["anthropic/claude-opus-5-5"]}},
            },
        },
        "agents": {"defaults": {"model": {"primary": LOCAL}}, "entries": {"main": {"model": LOCAL}}},
    }
    proposed = build_config(machine(), current=current)
    merged = merged_document(current, proposed)

    subagent = merged["plugins"]["entries"]["memory-core"]["subagent"]
    assert subagent["allowedModels"] == ["anthropic/claude-opus-5-5", GO]


def test_the_default_model_is_the_system_agents_before_the_default_key():
    config = {
        "agents": {
            "defaults": {"model": {"primary": LOCAL}, "systemAgent": {"agentId": "capo"}},
            "entries": {"main": {"model": LOCAL}, "capo": {"model": GO}},
        },
    }
    assert background.default_model(config) == GO
    assert background.default_model({"agents": {"defaults": {"model": {"primary": LOCAL}}}}) == LOCAL


def test_the_doctor_names_only_the_jobs_still_on_a_local_model():
    half = {
        "agents": {
            "defaults": {"model": {"primary": LOCAL}, "compaction": {"model": GO}},
            "entries": {"main": {"model": LOCAL}},
        },
    }
    line = background.report(half)
    assert line.severity is Severity.WARN
    assert "dreaming" in line.message
    assert "compaction" not in line.message
    assert "run 'nexgen openclaw setup'" in line.action.lower()


def test_a_machine_with_no_local_job_gets_one_pass_line():
    cloud = {"agents": {"defaults": {"model": {"primary": GO}}, "entries": {"main": {"model": GO}}}}
    line = background.report(cloud)
    assert line.severity is Severity.OK
    assert line.action is None
