"""Tests for OpenClaw squad as data, name preservation, SOUL.md, and model keys."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from nexgen_core.openclaw.detect import Detection, Installation
from nexgen_core.openclaw.profile import build_config
from nexgen_core.openclaw.setup_steps import Setup
from nexgen_core.openclaw.squad import (
    apply_squad,
    get_squad_souls,
    load_squad,
    register_model_keys,
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
