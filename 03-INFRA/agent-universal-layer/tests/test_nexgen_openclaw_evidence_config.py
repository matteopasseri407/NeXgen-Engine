"""The evidence switch: what the deposit writes, and what the doctor reads back.

The two sides are proven together on purpose. `install_guardrail` derives the section from the
configuration in force, the plugin reads it on every call, and the doctor says whether it is on
and for which local models: a section neither side agrees on is the failure this file exists to
catch. No gateway, no model, no network.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from conftest import REAL_UL  # noqa: E402 - path shim first, then the engine
from nexgen_core.openclaw.detect import (  # noqa: E402 - sys.path shim for cloned checkout
    Detection, Installation, LocalModelInfo, LocalModels, Login,
)
from nexgen_core.openclaw.doctor import collect  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.evidence import evidence_section, local_refs  # noqa: E402
from nexgen_core.openclaw.plan import build_plan  # noqa: E402
from nexgen_core.openclaw.profile import Choices  # noqa: E402
from nexgen_core.openclaw.setup_write import merged_document  # noqa: E402
from nexgen_core.report import Severity  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.runtimes.openclaw import PLUGIN_ID, OpenClawRuntime  # noqa: E402

HOOKS = REAL_UL / "hooks"


def local_config() -> dict:
    """A configuration with one Ollama model and one llama.cpp model, both used by an agent."""
    return {
        "plugins": {"allow": ["ollama", "llama-cpp"], "entries": {"ollama": {"enabled": True}}},
        "models": {"providers": {
            "ollama": {"api": "ollama", "models": [{"id": "qwen3:8b"}]},
            "llama-cpp": {"api": "openai-completions", "models": [{"id": "gemma-3-12b"}]},
        }},
        "agents": {"entries": {"main": {"model": "anthropic/claude-opus-5-5"}}},
    }


def cloud_config() -> dict:
    """No local model anywhere: nothing for the check to watch."""
    return {
        "plugins": {"allow": ["anthropic"], "entries": {}},
        "models": {"providers": {"anthropic": {"api": "anthropic", "models": [{"id": "claude-opus-5-5"}]}}},
        "agents": {"entries": {"main": {"model": "anthropic/claude-opus-5-5"}}},
    }


def a_home(tmp_path: Path, config: dict | None) -> Path:
    home = tmp_path / "home"
    state = home / ".openclaw"
    state.mkdir(parents=True)
    if config is not None:
        (state / "openclaw.json").write_text(json.dumps(config), encoding="utf-8")
    return home


def plugin_dir(home: Path) -> Path:
    return home / ".openclaw" / "extensions" / PLUGIN_ID


def deposit(home: Path, tmp_path: Path, *, before: dict | None = None, after: dict | None = None) -> dict:
    """Deposit the plugin, optionally around a switch file the earlier run left behind."""
    body = tmp_path / "guardrail-body.mjs"
    body.write_text("// the private policy, in the test only\n", encoding="utf-8")
    path = plugin_dir(home) / "nexgen.config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if before is not None:
        path.write_text(json.dumps(before), encoding="utf-8")
    OpenClawRuntime().install_guardrail(home, body, HOOKS)
    if after is not None:
        path.write_text(json.dumps(after), encoding="utf-8")
    return json.loads(path.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ the deposit side


def test_the_deposit_names_the_local_models_the_configuration_serves(tmp_path):
    home = a_home(tmp_path, local_config())

    section = deposit(home, tmp_path)["evidence"]

    assert section == {"enabled": True, "providers": ["llama-cpp", "ollama"]}


def test_a_cloud_only_machine_deposits_the_switch_off(tmp_path):
    home = a_home(tmp_path, cloud_config())

    section = deposit(home, tmp_path)["evidence"]

    assert section == {"enabled": False, "providers": []}


def test_the_switch_is_merged_and_takes_nothing_away(tmp_path):
    """A section the earlier sync wrote is replaced; every other switch stays where it was."""
    previous = {"posture": "ask", "operator": {"note": "keep me"}, "evidence": {"enabled": True, "providers": ["ollama"]}}
    home = a_home(tmp_path, cloud_config())

    config = deposit(home, tmp_path, before=previous)

    assert config["posture"] == "ask"
    assert config["operator"] == {"note": "keep me"}
    assert config["evidence"] == {"enabled": False, "providers": []}


def test_a_local_model_named_only_by_an_agent_counts(tmp_path):
    config = local_config()
    config["models"]["providers"] = {}
    config["agents"]["entries"]["capo"] = {"model": "ollama/qwen3:8b"}

    assert local_refs(config) == ["ollama/qwen3:8b"]
    assert evidence_section(config) == {"enabled": True, "providers": ["ollama"]}


def test_an_unreadable_configuration_has_nothing_to_watch(tmp_path):
    assert evidence_section(None) == {"enabled": False, "providers": []}


# -------------------------------------------------------------------- the doctor line


def machine(config_path: Path) -> Detection:
    return Detection(
        openclaw=Installation(installed=True, version="2026.10.6", config_path=str(config_path), config_present=config_path.is_file()),
        claude=Login(binary=False),
        codex=Login(binary=False),
        opencode_go=True,
        local=LocalModels(ollama=(LocalModelInfo(name="qwen3:8b"),)),
        n8n=False,
    )


def outcomes(tmp_path: Path, config: dict) -> dict:
    state = tmp_path / "home" / ".openclaw"
    state.mkdir(parents=True, exist_ok=True)
    path = state / "openclaw.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    vault = tmp_path / "vault" / "03-INFRA" / "agent-universal-layer"
    vault.mkdir(parents=True, exist_ok=True)
    (vault / "modules.state.yaml").write_text("schema_version: 2\nmodules:\n  openclaw: local\n", encoding="utf-8")
    found = collect(
        detected=machine(path),
        home=tmp_path / "home",
        vault_data=tmp_path / "vault",
        engine_root=tmp_path / "engine",
        env={"LOCALAPPDATA": str(tmp_path / "localappdata"), "APPDATA": str(tmp_path / "appdata")},
        platform="linux",
        connect=lambda host, port, timeout: False,
        wsl_distributions=lambda: [],
        node_version=lambda: "v24.22.1",
    )
    return {outcome.id: outcome for outcome in found}


def enabled_config() -> dict:
    config = local_config()
    config["plugins"]["entries"][PLUGIN_ID] = {
        "enabled": True,
        "hooks": {"allowConversationAccess": True},
    }
    return config


def test_the_doctor_says_the_check_is_on_and_for_which_local_models(tmp_path):
    home = a_home(tmp_path, enabled_config())
    deposit(home, tmp_path)

    evidence = outcomes(tmp_path, enabled_config())["openclaw.evidence"]

    assert evidence.severity == Severity.OK
    assert "llama-cpp" in evidence.message and "ollama" in evidence.message


def test_the_doctor_names_a_switch_that_was_never_deposited(tmp_path):
    a_home(tmp_path, enabled_config())

    evidence = outcomes(tmp_path, enabled_config())["openclaw.evidence"]

    assert evidence.severity == Severity.WARN
    assert "nexgen sync" in evidence.action


def test_the_doctor_keeps_the_off_cases_apart(tmp_path):
    """No local model and no plugin are both fine, and they are not the same sentence."""
    off_config = cloud_config()
    off_config["plugins"]["entries"][PLUGIN_ID] = {"enabled": True}
    off_home = a_home(tmp_path / "cloud", off_config)
    deposit(off_home, tmp_path / "cloud")

    off = outcomes(tmp_path / "cloud", off_config)["openclaw.evidence"]
    absent_home = a_home(tmp_path / "absent", local_config())
    deposit(absent_home, tmp_path / "absent")
    absent = outcomes(tmp_path / "absent", local_config())["openclaw.evidence"]

    assert off.severity == absent.severity == Severity.OK
    assert off.message != absent.message
    for outcome in (off, absent):
        assert "ollama" not in outcome.message and "llama-cpp" not in outcome.message


# ------------------------------------------------------- the permission the check needs


def test_the_doctor_names_a_conversation_the_plugin_may_not_read(tmp_path):
    """A check that covers a provider and is never handed an answer is a check that does nothing."""
    config = enabled_config()
    config["plugins"]["entries"][PLUGIN_ID].pop("hooks")
    home = a_home(tmp_path, config)
    deposit(home, tmp_path)

    evidence = outcomes(tmp_path, config)["openclaw.evidence"]

    assert evidence.severity == Severity.WARN
    assert "allowConversationAccess" in evidence.action


def test_the_plan_grants_the_conversation_access_the_check_needs(tmp_path):
    """`setup` writes what the plan prints, and the plugin is useless without this grant."""
    home = a_home(tmp_path, local_config())

    plan = build_plan(
        detected=machine(home / ".openclaw" / "openclaw.json"),
        choices=Choices(),
        home=home,
        vault_data=tmp_path / "vault",
        engine_root=tmp_path / "engine",
        plugin_deposited=True,
    )

    entry = plan.config["plugins"]["entries"][PLUGIN_ID]
    assert entry["enabled"] is True
    assert entry["hooks"]["allowConversationAccess"] is True


def test_the_grant_is_merged_into_the_hooks_block_already_there(tmp_path):
    """Nothing the person wrote in that block is removed to make room for the grant."""
    config = local_config()
    config["plugins"]["entries"][PLUGIN_ID] = {
        "enabled": False,
        "hooks": {"allowPromptInjection": False, "timeoutMs": 30000},
    }
    home = a_home(tmp_path, config)

    plan = build_plan(
        detected=machine(home / ".openclaw" / "openclaw.json"),
        choices=Choices(),
        home=home,
        vault_data=tmp_path / "vault",
        engine_root=tmp_path / "engine",
        plugin_deposited=True,
    )

    written = merged_document(plan.current, plan.config)

    assert written["plugins"]["entries"][PLUGIN_ID]["hooks"] == {
        "allowPromptInjection": False,
        "timeoutMs": 30000,
        "allowConversationAccess": True,
    }



def test_the_plan_finds_the_squad_in_the_vault_when_no_vault_is_passed(tmp_path: Path, monkeypatch) -> None:
    """`nexgen openclaw setup` passes no vault: the squad in the user's vault still applies."""
    home = a_home(tmp_path, local_config())
    vault = tmp_path / "vault"
    (vault / "03-INFRA").mkdir(parents=True)
    (vault / "03-INFRA" / "squad.yaml").write_text(
        'default: "main"\nagents:\n  main:\n    name: "Agente Prova"\n    model: "anthropic/claude-sonnet-5-5"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_VAULT_DATA", str(vault))

    plan = build_plan(
        detected=machine(home / ".openclaw" / "openclaw.json"),
        choices=Choices(),
        home=home,
        engine_root=tmp_path / "engine",
        plugin_deposited=True,
    )

    assert plan.config["agents"]["entries"]["main"]["name"] == "Agente Prova"
