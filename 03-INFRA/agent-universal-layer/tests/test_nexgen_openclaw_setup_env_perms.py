"""Tests for OpenClaw state permissions, plugin deposit verification, and .env."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from nexgen_core.openclaw.setup_service_env import (
    extract_mounted_mcp_env_vars,
    extract_referenced_env_vars,
    write_gateway_service_env,
)
from nexgen_core.openclaw.setup_steps import (
    Setup,
    fix_state_permissions,
    is_plugin_verified,
    plugin_step,
)


def _make_dummy_setup(tmp_path: Path, platform: str = "linux") -> Setup:
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    config_path = state_dir / "openclaw.json"

    service_mock = MagicMock()
    service_mock.status.return_value = {"installed": True, "mechanism": "systemd"}
    service_mock.port_probe.return_value = True

    return Setup(
        home=home,
        env={
            "ANTHROPIC_API_KEY": "sk-ant-test1234",
            "OPENAI_API_KEY": "sk-proj-test1234",
            "BRAVE_API_KEY": "brave-test1234",
            "UNUSED_SECRET": "secret-never-written",
        },
        config_path=config_path,
        engine_root=tmp_path / "engine",
        vault_data=tmp_path / "vault",
        pinned="2026.10.6",
        dry_run=False,
        out=MagicMock(),
        which=lambda cmd: "/bin/" + cmd,
        run=MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr="")),
        service=service_mock,
        stamp="20261024-test",
        platform=platform,
    )


def test_extract_referenced_env_vars() -> None:
    config = {
        "agents": {
            "entries": {
                "capo": {"model": "anthropic/claude-opus-5-5"},
                "custom": {"model": "custom/provider", "prompt": "Hello ${CUSTOM_TOKEN}"},
            },
        },
        "mcp": {
            "servers": {
                "searxng": {
                    "env": {
                        "SEARXNG_URL": "http://127.0.0.1:8888",
                        "BRAVE_API_KEY": "${BRAVE_API_KEY}",
                    },
                },
            },
        },
    }
    extracted = extract_referenced_env_vars(config)
    assert "ANTHROPIC_API_KEY" in extracted
    assert "CUSTOM_TOKEN" in extracted
    assert "SEARXNG_URL" in extracted
    assert "BRAVE_API_KEY" in extracted
    assert "UNUSED_SECRET" not in extracted


def test_write_gateway_service_env_new_file(tmp_path: Path) -> None:
    setup = _make_dummy_setup(tmp_path, platform="linux")
    config = {
        "agents": {"entries": {"capo": {"model": "anthropic/claude-opus-5-5"}}},
        "tools": {"web": "${BRAVE_API_KEY}"},
    }

    env_file = write_gateway_service_env(setup, config)
    assert env_file == setup.state_dir / ".env"
    assert env_file.is_file()

    content = env_file.read_text(encoding="utf-8")
    assert 'ANTHROPIC_API_KEY="sk-ant-test1234"' in content
    assert 'BRAVE_API_KEY="brave-test1234"' in content
    assert "UNUSED_SECRET" not in content

    # Verify no secret values were printed in setup.out
    for call in setup.out.call_args_list:
        msg = str(call[0][0])
        assert "sk-ant-test1234" not in msg
        assert "brave-test1234" not in msg
        assert "secret-never-written" not in msg


def test_write_gateway_service_env_realigns_the_keys_it_owns(tmp_path: Path) -> None:
    """A referenced key is a derivative of the secret store, not a value frozen at install time.

    The old contract preserved whatever the file already held, so a token rotated in
    the encrypted store stayed alive here at its previous value: two copies of one
    secret. Keys the engine does not reference are the person's and stay.
    """
    setup = _make_dummy_setup(tmp_path, platform="linux")
    env_file = setup.state_dir / ".env"
    env_file.write_text('ANTHROPIC_API_KEY="user-own-custom-key"\nCUSTOM_VAR="preserved"\n', encoding="utf-8")

    config = {
        "agents": {"entries": {"capo": {"model": "anthropic/claude-opus-5-5"}}},
        "tools": {"web": "${BRAVE_API_KEY}"},
    }

    res_file = write_gateway_service_env(setup, config)
    assert res_file == env_file

    content = env_file.read_text(encoding="utf-8")
    # The referenced key carries the value in force, and the old one is gone.
    assert 'ANTHROPIC_API_KEY="sk-ant-test1234"' in content
    assert "user-own-custom-key" not in content
    # A key the engine knows nothing about is the person's and is left alone.
    assert 'CUSTOM_VAR="preserved"' in content
    # Missing referenced key was appended
    assert 'BRAVE_API_KEY="brave-test1234"' in content

    # Verify no secret values in output
    for call in setup.out.call_args_list:
        msg = str(call[0][0])
        assert "user-own-custom-key" not in msg
        assert "brave-test1234" not in msg


def test_the_gateway_env_covers_the_variables_of_the_mounted_mcp_servers(tmp_path: Path) -> None:
    """A stdio server names its variables in its arguments and its `env` block.

    The MCP table is written into `openclaw.json` by `nexgen mcp render`, not by
    the setup, so the proposal this writer receives does not carry it: without
    reading the file in force, a bridge or a connector starts with an empty
    value and the gateway never notices.
    """
    setup = _make_dummy_setup(tmp_path, platform="linux")
    setup.env.update({
        "AGENT_ENGINE_ROOT": "/opt/engine",
        "BRIDGE_TOKEN": "bridge-secret",
        "MCP_CLIENT_ID": "client-id",
        "NOT_AN_MCP_VAR": "read-from-somewhere-else",
    })
    setup.config_path.write_text(
        json.dumps({
            "mcp": {
                "servers": {
                    "playwright": {
                        "command": "node",
                        "args": ["${AGENT_ENGINE_ROOT}/agent-universal-layer/mcp/playwright-human-safe.mjs"],
                    },
                    "bridge": {
                        "command": "node",
                        "args": [
                            "${AGENT_ENGINE_ROOT}/agent-universal-layer/mcp/mcp-http-bridge.mjs",
                            "https://bridge.example/mcp",
                        ],
                        "env": {"AUTH_HEADER": "${BRIDGE_TOKEN}", "MCP_CLIENT_ID": "${MCP_CLIENT_ID}"},
                    },
                },
            },
            "agents": {"entries": {"main": {"prompt": "${NOT_AN_MCP_VAR}"}}},
        }),
        encoding="utf-8",
    )

    env_file = write_gateway_service_env(setup, {"agents": {"entries": {}}})

    content = env_file.read_text(encoding="utf-8")
    assert 'AGENT_ENGINE_ROOT="/opt/engine"' in content
    assert 'BRIDGE_TOKEN="bridge-secret"' in content
    assert 'MCP_CLIENT_ID="client-id"' in content
    # Only the mounted server table is read: the rest of the file is not a variable source.
    assert "NOT_AN_MCP_VAR" not in content


def test_a_corrupt_config_adds_no_server_variable_instead_of_crashing(tmp_path: Path) -> None:
    setup = _make_dummy_setup(tmp_path, platform="linux")
    setup.config_path.write_text("{not json", encoding="utf-8")

    env_file = write_gateway_service_env(setup, {"tools": {"web": "${BRAVE_API_KEY}"}})

    content = env_file.read_text(encoding="utf-8")
    assert 'BRAVE_API_KEY="brave-test1234"' in content
    assert extract_mounted_mcp_env_vars(setup.config_path) == set()


def test_fix_state_permissions(tmp_path: Path) -> None:
    setup = _make_dummy_setup(tmp_path, platform="linux")
    sample_file = setup.state_dir / "openclaw.json"
    sample_file.write_text("{}", encoding="utf-8")

    res = fix_state_permissions(setup)
    assert res is True


def test_plugin_step_when_plugin_already_present(tmp_path: Path) -> None:
    setup = _make_dummy_setup(tmp_path)
    plugin_dir = setup.state_dir / "extensions" / "nexgen"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "openclaw.plugin.json").write_text("{}", encoding="utf-8")
    (plugin_dir / "index.mjs").write_text("// plugin", encoding="utf-8")

    assert is_plugin_verified(setup) is True
    assert plugin_step(setup) is True


def test_is_plugin_verified_requires_manifest_and_index(tmp_path: Path) -> None:
    setup = _make_dummy_setup(tmp_path)
    plugin_dir = setup.state_dir / "extensions" / "nexgen"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "index.mjs").write_text("// ok", encoding="utf-8")
    assert is_plugin_verified(setup) is False
    (plugin_dir / "openclaw.plugin.json").write_text("{}", encoding="utf-8")
    assert is_plugin_verified(setup) is True


def test_plugin_step_deposits_and_verifies_plugin(tmp_path: Path) -> None:
    setup = _make_dummy_setup(tmp_path)
    plugin_dir = setup.state_dir / "extensions" / "nexgen"
    assert not plugin_dir.exists()

    hooks_dir = setup.engine_root / "03-INFRA" / "agent-universal-layer" / "hooks"
    plugin_src = hooks_dir / "openclaw-guardrail-plugin"
    plugin_src.mkdir(parents=True, exist_ok=True)
    (plugin_src / "openclaw.plugin.json").write_text('{"id": "nexgen"}', encoding="utf-8")
    (plugin_src / "index.mjs").write_text("// entrypoint", encoding="utf-8")
    (hooks_dir / "nexgen-guardrail-core.mjs").write_text("// core", encoding="utf-8")
    (hooks_dir / "vault-briefing.txt").write_text("briefing", encoding="utf-8")

    assert plugin_step(setup) is True
    assert (plugin_dir / "openclaw.plugin.json").is_file()
    assert (plugin_dir / "index.mjs").is_file()
    assert is_plugin_verified(setup) is True


def test_plugin_step_retries_and_errors_when_deposit_fails(tmp_path: Path, monkeypatch) -> None:
    setup = _make_dummy_setup(tmp_path)
    monkeypatch.setattr("nexgen_core.openclaw.setup_steps._find_engine_hooks_dir", lambda r: None)
    assert plugin_step(setup) is False
    calls = [str(c[0][0]) for c in setup.out.call_args_list]
    assert any("retrying once" in msg for msg in calls)
    assert any("Error: engine plugin nexgen could not be deposited or verified." in msg for msg in calls)
