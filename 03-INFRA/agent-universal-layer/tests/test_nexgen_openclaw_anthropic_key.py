"""An Anthropic agent on `claude-cli` needs no API key, and the gateway must not carry one.

OpenClaw's live model discovery calls `GET /v1/models` with `ANTHROPIC_API_KEY`
whenever the key is resolvable in the gateway's environment. A workspace-scoped
key answers 400 there, OpenClaw records the provider as `unavailable`, and every
app that reads `models.list` says it cannot update the models on each open. An
agent on `claude-cli` signs in through Claude Code, so the key is not what runs
its turns: the setup must not write it, and the one already written must go.

A reference the configuration really makes (`${ANTHROPIC_API_KEY}` in a prompt, a
mount, an agent on another runtime) is a different case and is honoured: the key
stays, because something reads it.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from nexgen_core.openclaw.setup_service_env import (
    extract_referenced_env_vars,
    write_gateway_service_env,
)

CLAUDE_CLI_PROVIDER = {"models": {"providers": {"anthropic": {"agentRuntime": {"id": "claude-cli"}}}}}


@dataclass
class FakeSetup:
    """The two fields the writer reads, and the words it says."""

    state_dir: Path
    env: dict[str, str]
    config_path: Path
    said: list[str]

    def out(self, line: str) -> None:
        self.said.append(line)


def _setup(tmp_path: Path) -> FakeSetup:
    state = tmp_path / ".openclaw"
    state.mkdir()
    config = state / "openclaw.json"
    config.write_text("{}", encoding="utf-8")
    return FakeSetup(state_dir=state, env={"ANTHROPIC_API_KEY": "sk-ant-workspace-scoped"},
                     config_path=config, said=[])


def _claude_cli_config() -> dict:
    """One agent on Claude Code's CLI, and nothing that names the key."""
    return {
        "agents": {"entries": {"capo": {"model": "anthropic/claude-opus-5-5"}}},
        **CLAUDE_CLI_PROVIDER,
    }


def test_a_claude_cli_agent_does_not_reference_the_api_key():
    assert "ANTHROPIC_API_KEY" not in extract_referenced_env_vars(_claude_cli_config())


def test_an_anthropic_agent_on_another_runtime_still_references_the_key():
    """The runtime is the whole reason: on any other runtime the key runs the turn."""
    config = {"agents": {"entries": {"capo": {"model": "anthropic/claude-opus-5-5"}}}}
    assert "ANTHROPIC_API_KEY" in extract_referenced_env_vars(config)


def test_a_reference_written_in_the_config_keeps_counting():
    """Someone who writes the reference on purpose is read, whatever the runtime."""
    config = _claude_cli_config()
    config["agents"]["entries"]["capo"]["prompt"] = "Study with ${ANTHROPIC_API_KEY}."
    assert "ANTHROPIC_API_KEY" in extract_referenced_env_vars(config)


def test_the_setup_does_not_write_the_key_for_a_claude_cli_agent(tmp_path: Path):
    setup = _setup(tmp_path)

    write_gateway_service_env(setup, _claude_cli_config())

    assert "ANTHROPIC_API_KEY" not in (setup.state_dir / ".env").read_text(encoding="utf-8")


def test_the_setup_writes_the_key_for_an_anthropic_agent_on_another_runtime(tmp_path: Path):
    """The person who needs the key keeps it: no claude-cli agent, nothing changes."""
    setup = _setup(tmp_path)
    config = {"agents": {"entries": {"capo": {"model": "anthropic/claude-opus-5-5"}}}}

    write_gateway_service_env(setup, config)

    assert 'ANTHROPIC_API_KEY="sk-ant-workspace-scoped"' in (setup.state_dir / ".env").read_text(encoding="utf-8")
