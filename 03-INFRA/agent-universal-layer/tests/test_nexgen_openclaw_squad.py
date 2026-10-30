"""Tests for OpenClaw squad as data, name preservation, SOUL.md, and model keys."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from nexgen_core.openclaw.detect import Detection, Installation
from nexgen_core.openclaw.profile import build_config
from nexgen_core.openclaw.setup_steps import Setup
from nexgen_core.openclaw.squad import (
    apply_squad,
    default_agent,
    get_squad_instructions,
    get_squad_souls,
    load_squad,
    register_model_keys,
    unresolved_default,
)


def _make_dummy_detection() -> Detection:
    det = MagicMock(spec=Detection)
    det.openclaw = Installation(installed=True, version="2026.10.6", config_path="")
    det.claude = MagicMock(available=True)
    det.codex = MagicMock(available=False)
    det.opencode_go = False
    det.n8n = False
    det.local = MagicMock(
        ollama=(),
        llamacpp=(),
        ollama_url="http://127.0.0.1:11434",
        llamacpp_url="http://127.0.0.1:8080",
    )
    return det


def test_load_and_apply_squad(tmp_path: Path) -> None:
    squad_file = tmp_path / "squad.yaml"
    squad_file.write_text(
        """
agents:
  main:
    name: "MyCustomBot"
    model: "anthropic/claude-opus-5-5"
    tools:
      agentToAgent:
        send: ["capo"]
  capo:
    name: "General"
    model: "anthropic/claude-opus-5-5"
    soul: "Always inspect before acting."
    tools:
      exec:
        host: "node"
        node: "pc-work"

bindings:
  - match:
      channel: "telegram"
    agentId: "main"
""",
        encoding="utf-8",
    )

    squad = load_squad(squad_file)
    assert squad is not None

    base_config: dict[str, dict] = {"agents": {"entries": {}}, "tools": {}}
    applied = apply_squad(base_config, squad)

    assert applied["agents"]["entries"]["main"]["name"] == "MyCustomBot"
    assert applied["agents"]["entries"]["capo"]["name"] == "General"
    assert applied["agents"]["entries"]["capo"]["tools"]["exec"]["node"] == "pc-work"
    assert "capo" in applied["tools"]["agentToAgent"]["allow"]
    assert "main" in applied["tools"]["agentToAgent"]["allow"]
    assert applied["bindings"][0]["agentId"] == "main"

    souls = get_squad_souls(squad)
    assert "capo" in souls
    assert "Always inspect before acting." in souls["capo"]
    assert "main" not in souls


def test_every_model_the_squad_names_is_selectable_from_every_agent(tmp_path: Path) -> None:
    """`model` on an agent is only its default: the others must be pickable on all of them."""
    squad = {
        "agents": {
            "main": {"model": "google-antigravity-cli/gemini-3.8-flash"},
            "capo": {"model": "anthropic/claude-opus-5-5"},
        },
        "models": {"openai/gpt-6-luna": {"agentRuntime": {"id": "codex"}}},
    }

    applied = apply_squad({"agents": {"entries": {}}, "tools": {}}, squad)

    defaults = applied["agents"]["defaults"]
    assert set(defaults["models"]) == {
        "google-antigravity-cli/gemini-3.8-flash", "anthropic/claude-opus-5-5", "openai/gpt-6-luna",
    }
    assert defaults["modelPolicy"]["allow"] == [
        "google-antigravity-cli/*", "anthropic/*", "openai/*",
    ]
    assert applied["agents"]["entries"]["main"]["model"] == "google-antigravity-cli/gemini-3.8-flash"


def test_a_squad_that_names_no_model_adds_no_policy(tmp_path: Path) -> None:
    applied = apply_squad({"agents": {"entries": {}}}, {"agents": {"main": {"name": "Solo"}}})

    assert "defaults" not in applied["agents"] or "modelPolicy" not in applied["agents"]["defaults"]


def test_the_squad_models_table_becomes_agents_defaults_models() -> None:
    """The root `models` object is strict: `mode`, `providers`, `catalogRefresh`, nothing else.

    An `entries` key there is refused by OpenClaw ("models: Unrecognized key: entries"),
    and `agents.defaults.models` is the record the per-model entries belong to.
    """
    squad = {
        "models": {
            "anthropic/claude-opus-5-5": {"agentRuntime": {"id": "claude-cli"}},
            "openai/gpt-6-luna": {"agentRuntime": {"id": "codex"}},
        },
    }

    applied = apply_squad({"agents": {"entries": {}}}, squad)

    assert "entries" not in applied.get("models", {})
    models = applied["agents"]["defaults"]["models"]
    assert models["anthropic/claude-opus-5-5"] == {"agentRuntime": {"id": "claude-cli"}}
    assert models["openai/gpt-6-luna"] == {"agentRuntime": {"id": "codex"}}


def test_a_model_entry_the_owner_wrote_keeps_every_key_the_squad_does_not_set() -> None:
    """The squad adds keys to a model's entry; it never replaces the entry under it."""
    config = {
        "agents": {
            "entries": {},
            "defaults": {
                "models": {
                    "anthropic/claude-opus-5-5": {"contextWindow": 200000},
                    "openai/gpt-6-luna": {"agentRuntime": {"id": "codex"}},
                },
            },
        },
    }
    squad = {"models": {"anthropic/claude-opus-5-5": {"agentRuntime": {"id": "claude-cli"}}}}

    models = apply_squad(config, squad)["agents"]["defaults"]["models"]

    assert models["anthropic/claude-opus-5-5"] == {
        "contextWindow": 200000,
        "agentRuntime": {"id": "claude-cli"},
    }
    assert models["openai/gpt-6-luna"] == {"agentRuntime": {"id": "codex"}}


def test_custom_agent_names_preserved_without_squad() -> None:
    detected = _make_dummy_detection()
    current = {
        "agents": {
            "entries": {
                "main": {"name": "PersonalAssistant", "identity": {"name": "PersonalAssistant"}},
                "capo": {"name": "StrategyLead", "identity": {"name": "StrategyLead"}},
            },
        },
    }

    config = build_config(detected, current=current)
    entries = config["agents"]["entries"]
    assert entries["main"]["identity"]["name"] == "PersonalAssistant"
    assert entries["capo"]["identity"]["name"] == "StrategyLead"


def test_register_model_keys(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    setup = Setup(
        home=home,
        env={"ANTHROPIC_API_KEY": "sk-ant-test-secret-key"},
        config_path=state_dir / "openclaw.json",
        engine_root=None,
        vault_data=None,
        pinned=None,
        dry_run=False,
        out=MagicMock(),
        which=lambda cmd: "/bin/" + cmd,
        run=MagicMock(),
        service=MagicMock(),
        stamp="test",
    )

    config = {
        "agents": {
            "entries": {
                "main": {"model": "anthropic/claude-opus-5-5"},
            },
        },
    }

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        count = register_model_keys(setup, config, openclaw_cmd="/bin/openclaw")
        assert count == 1
        assert mock_run.called
        call_args = mock_run.call_args
        assert call_args[0][0] == [
            "/bin/openclaw",
            "models",
            "auth",
            "paste-api-key",
            "--agent",
            "main",
            "--provider",
            "anthropic",
        ]
        assert call_args[1]["input"] == "sk-ant-test-secret-key\n"


def test_the_squad_names_the_default_agent(tmp_path: Path) -> None:
    """Without this owner, OpenClaw refuses the main session on a multi-agent roster."""
    squad = {
        "default": "capo",
        "agents": {"main": {"name": "assistente"}, "capo": {"name": "capo"}},
    }

    applied = apply_squad({"agents": {"entries": {}}}, squad)

    assert applied["agents"]["defaults"]["systemAgent"]["agentId"] == "capo"
    # The legacy marker a squad may still carry is read, never written back.
    assert "default" not in applied["agents"]["entries"]["capo"]


def test_the_first_marked_agent_is_the_default_when_none_is_named() -> None:
    squad = {
        "agents": {
            "main": {"name": "assistente"},
            "capo": {"name": "capo", "default": True},
            "sviluppo": {"name": "sviluppo", "default": True},
        },
    }

    assert default_agent(squad) == "capo"
    assert apply_squad({"agents": {"entries": {}}}, squad)["agents"]["defaults"]["systemAgent"]["agentId"] == "capo"


def test_a_default_the_squad_does_not_declare_writes_nobody() -> None:
    """An owner OpenClaw cannot resolve is the refusal this field exists to prevent."""
    squad = {"default": "sviluppo", "agents": {"main": {"name": "assistente"}}}

    applied = apply_squad({"agents": {"entries": {}}}, squad)

    assert "systemAgent" not in applied["agents"].get("defaults", {})
    assert unresolved_default(squad) == "sviluppo"
    assert unresolved_default({"agents": {"main": {}}}) is None


def test_a_default_already_in_force_survives_a_squad_that_names_none() -> None:
    current = {"agents": {"entries": {}, "defaults": {"systemAgent": {"agentId": "main"}}}}

    applied = apply_squad(current, {"agents": {"capo": {"name": "capo"}}})

    assert applied["agents"]["defaults"]["systemAgent"]["agentId"] == "main"


def test_an_agent_exec_policy_lands_under_tools_exec() -> None:
    """`security`/`ask` are translated into the one `mode` the gateway reads today."""
    squad = {
        "agents": {
            "sviluppo": {
                "name": "sviluppo",
                "exec": {
                    "security": "full",
                    "ask": "off",
                    "host": "node",
                    "node": "node-pc",
                    "notAKey": "dropped",
                },
            },
        },
    }

    entry = apply_squad({"agents": {"entries": {}}}, squad)["agents"]["entries"]["sviluppo"]

    assert entry["tools"]["exec"] == {
        "mode": "full",
        "host": "node",
        "node": "node-pc",
    }


def test_every_exec_policy_becomes_the_mode_openclaw_reads() -> None:
    """`mode` replaced the older pair; the engine writes the normalized field.

    `ask=always` is a human-reviewed miss, `ask=on-miss` a classifier-reviewed one,
    and with `ask=off` the security level is the whole policy.
    """
    cases = {
        ("full", "off"): "full",
        ("allowlist", "off"): "allowlist",
        ("deny", "off"): "deny",
        ("full", "always"): "ask",
        ("allowlist", "on-miss"): "auto",
    }
    for (security, ask), expected in cases.items():
        squad = {"agents": {"x": {"name": "x", "exec": {"security": security, "ask": ask}}}}
        entry = apply_squad({"agents": {"entries": {}}}, squad)["agents"]["entries"]["x"]
        assert entry["tools"]["exec"] == {"mode": expected}, (security, ask)


def test_an_explicit_mode_wins_and_the_legacy_pair_is_dropped() -> None:
    squad = {
        "agents": {
            "x": {"name": "x", "exec": {"mode": "ask", "security": "full", "ask": "off"}},
        },
    }

    entry = apply_squad({"agents": {"entries": {}}}, squad)["agents"]["entries"]["x"]

    assert entry["tools"]["exec"] == {"mode": "ask"}


def test_a_policy_with_only_ask_is_not_guessed_into_a_mode() -> None:
    """With no security level there is no honest mode: the declared key survives."""
    squad = {"agents": {"x": {"name": "x", "exec": {"ask": "off"}}}}

    entry = apply_squad({"agents": {"entries": {}}}, squad)["agents"]["entries"]["x"]

    assert entry["tools"]["exec"] == {"ask": "off"}


def test_a_nested_exec_block_and_an_agent_policy_merge_per_key() -> None:
    squad = {
        "agents": {
            "sviluppo": {
                "name": "sviluppo",
                "exec": {"ask": "off"},
                "tools": {"exec": {"host": "node", "node": "node-pc", "ask": "on-miss"}},
            },
        },
    }

    entry = apply_squad({"agents": {"entries": {}}}, squad)["agents"]["entries"]["sviluppo"]

    assert entry["tools"]["exec"] == {"host": "node", "node": "node-pc", "ask": "off"}


def test_the_squad_instructions_are_read_from_beside_the_squad_file(tmp_path: Path) -> None:
    """`agents_md` is that agent's own text; `common_md` is appended to everyone."""
    (tmp_path / "roles").mkdir()
    (tmp_path / "roles" / "sviluppo.md").write_text("# sviluppo\n\nOwn the code.\n", encoding="utf-8")
    (tmp_path / "common.md").write_text("## Common\n\nSpeak plainly.\n", encoding="utf-8")
    squad_file = tmp_path / "squad.yaml"
    squad_file.write_text(
        """
common_md: "common.md"
agents:
  sviluppo:
    agents_md: "roles/sviluppo.md"
  main:
    name: "assistente"
""",
        encoding="utf-8",
    )

    squad = load_squad(squad_file)
    instructions, unreadable = get_squad_instructions(squad, squad_file)

    assert unreadable == ()
    assert set(instructions) == {"sviluppo", "main"}
    assert "Own the code." in instructions["sviluppo"]
    assert "Speak plainly." in instructions["sviluppo"]
    assert "Speak plainly." in instructions["main"]
    assert "Own the code." not in instructions["main"]
    assert instructions["main"].startswith("<!-- nexgen:squad:begin")


def test_a_declared_instruction_file_that_is_missing_is_named_not_guessed(tmp_path: Path) -> None:
    squad_file = tmp_path / "squad.yaml"
    squad_file.write_text(
        """
common_md: "missing-common.md"
agents:
  main:
    agents_md: "missing-role.md"
""",
        encoding="utf-8",
    )

    instructions, unreadable = get_squad_instructions(load_squad(squad_file), squad_file)

    assert instructions == {}
    assert len(unreadable) == 2
    assert str(tmp_path / "missing-common.md") in unreadable
    assert str(tmp_path / "missing-role.md") in unreadable

