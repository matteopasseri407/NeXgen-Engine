"""The doctor names the key that makes the Anthropic catalogue fail on every open.

OpenClaw's live discovery calls `GET /v1/models` whenever `ANTHROPIC_API_KEY` is
resolvable in the gateway's environment, and a workspace-scoped key answers 400:
the provider is recorded as unavailable and every app that reads `models.list`
says it cannot update the models. An agent on `claude-cli` does not read that key,
so its presence is a fault with one action and no ambiguity.

The check is silent where it has nothing to judge: a machine without OpenClaw, or
with no agent on `claude-cli`, is another check's answer, and the gateway's
environment is not the engine's business there.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from nexgen_core import i18n
from nexgen_core.checks.anthropic_catalog_checks import check_anthropic_catalog
from nexgen_core.paths import openclaw_config
from nexgen_core.report import Severity

CLAUDE_REF = "anthropic/claude-opus-5-5"


@pytest.fixture(autouse=True)
def _english():
    i18n.set_language("en")
    yield
    i18n.set_language(None)


@pytest.fixture(autouse=True)
def _no_openclaw_env(monkeypatch):
    monkeypatch.delenv("OPENCLAW_CONFIG_PATH", raising=False)
    monkeypatch.delenv("OPENCLAW_STATE_DIR", raising=False)


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    return home


def _claude_cli_config() -> dict:
    """One agent on Claude Code's CLI; nothing else names the key."""
    return {
        "agents": {
            "defaults": {"models": {CLAUDE_REF: {"agentRuntime": {"id": "claude-cli"}}}},
            "entries": {"capo": {"model": CLAUDE_REF}},
        },
    }


def _write(home: Path, config: dict | None = None) -> Path:
    path = openclaw_config(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config or _claude_cli_config()), encoding="utf-8")
    return path


def _key_in(home: Path, name: str, value: str = "sk-ant-workspace-scoped") -> Path:
    path = openclaw_config(home).parent / name
    path.write_text(f'ANTHROPIC_API_KEY="{value}"\n', encoding="utf-8")
    return path


def test_the_doctor_names_a_key_left_in_the_environment_file(tmp_path: Path) -> None:
    home = _home(tmp_path)
    _write(home)
    _key_in(home, ".env")

    outcome = check_anthropic_catalog(home)

    assert outcome is not None
    assert outcome.severity == Severity.WARN
    assert outcome.id == "openclaw.anthropic_catalog"
    assert "models" in outcome.message
    assert "sk-ant-workspace-scoped" not in outcome.message
    assert "nexgen openclaw setup" in outcome.action
    assert "nexgen openclaw restart" in outcome.action
    assert "openclaw gateway install --force" not in outcome.action


def test_the_doctor_names_a_key_left_in_the_systemd_copy(tmp_path: Path) -> None:
    """The service reads its own copy, so a clean `.env` is not enough."""
    home = _home(tmp_path)
    _write(home)
    _key_in(home, "gateway.systemd.env")

    outcome = check_anthropic_catalog(home)

    assert outcome is not None
    assert outcome.severity == Severity.WARN
    assert "gateway.systemd.env" in outcome.message
    # The copy is named with its path: the engine never writes it, so the line is
    # the person's to remove, and a forced reinstall is not the answer.
    assert "gateway.systemd.env" in outcome.action
    assert "nexgen openclaw setup" in outcome.action
    assert "nexgen openclaw restart" in outcome.action
    assert "openclaw gateway install --force" not in outcome.action


def test_the_doctor_is_silent_without_openclaw(tmp_path: Path) -> None:
    assert check_anthropic_catalog(_home(tmp_path)) is None


def test_the_doctor_is_silent_when_no_agent_runs_on_claude_cli(tmp_path: Path) -> None:
    """Zero difference for a person who reads the key: their machine is not this one."""
    home = _home(tmp_path)
    _write(home, {"agents": {"entries": {"capo": {"model": CLAUDE_REF}}}})
    _key_in(home, ".env")

    assert check_anthropic_catalog(home) is None


def test_the_doctor_is_silent_when_a_mounted_server_names_the_key(tmp_path: Path) -> None:
    """A reference somebody wrote on purpose is read, not overruled."""
    home = _home(tmp_path)
    config = _claude_cli_config()
    config["mcp"] = {"servers": {"x": {"env": {"TOKEN": "${ANTHROPIC_API_KEY}"}}}}
    _write(home, config)
    _key_in(home, ".env")

    assert check_anthropic_catalog(home) is None


def test_the_doctor_passes_a_gateway_environment_without_the_key(tmp_path: Path) -> None:
    home = _home(tmp_path)
    _write(home)
    (openclaw_config(home).parent / ".env").write_text('OTHER="stays"\n', encoding="utf-8")

    outcome = check_anthropic_catalog(home)

    assert outcome is not None
    assert outcome.severity == Severity.OK


def test_the_doctor_registers_the_check_beside_the_loopback_one(tmp_path: Path) -> None:
    """A check nobody runs answers nothing: the registration is part of the behaviour."""
    from nexgen_core import doctor

    registered: dict[str, object] = {}
    doctor._run_harness_checks(lambda name, thunk: registered.__setitem__(name, thunk),
                               tmp_path, tmp_path, tmp_path)

    assert "openclaw.anthropic_catalog" in registered
    assert "openclaw.claude_loopback" in registered
