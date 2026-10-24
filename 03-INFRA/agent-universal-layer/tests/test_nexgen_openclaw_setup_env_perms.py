"""Tests for OpenClaw state permissions, plugin deposit verification, and gateway.systemd.env."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from nexgen_core.openclaw.setup_service_env import (
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


def test_write_gateway_service_env(tmp_path: Path) -> None:
    setup = _make_dummy_setup(tmp_path, platform="linux")
    systemd_user_dir = setup.home / ".config" / "systemd" / "user"
    systemd_user_dir.mkdir(parents=True, exist_ok=True)

    config = {
        "agents": {"entries": {"capo": {"model": "anthropic/claude-opus-5-5"}}},
        "tools": {"web": "${BRAVE_API_KEY}"},
    }

    env_file = write_gateway_service_env(setup, config, systemd_user_dir=systemd_user_dir)
    assert env_file is not None
    assert env_file.is_file()

    content = env_file.read_text(encoding="utf-8")
    assert 'ANTHROPIC_API_KEY="sk-ant-test1234"' in content
    assert 'BRAVE_API_KEY="brave-test1234"' in content
    assert "UNUSED_SECRET" not in content

    # Check drop-in unit
    dropin = systemd_user_dir / "openclaw-gateway.service.d" / "10-env.conf"
    assert dropin.is_file()
    assert "EnvironmentFile=%h/" in dropin.read_text(encoding="utf-8")


def test_fix_state_permissions(tmp_path: Path) -> None:
    setup = _make_dummy_setup(tmp_path, platform="linux")
    # Simulate a file and directory
    sample_file = setup.state_dir / "openclaw.json"
    sample_file.write_text("{}", encoding="utf-8")

    # Call fix_state_permissions
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
