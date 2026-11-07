"""The gateway's environment is a derivative of the secret store, never a second one.

OpenClaw reads its variables from `$OPENCLAW_STATE_DIR/.env`. The setup used to add
the missing keys and leave the present ones alone, so a token rotated in the engine's
encrypted store kept answering from the old value here: the same secret alive twice,
which is what the store exists to prevent.

The systemd unit OpenClaw generates reads a second copy, `gateway.systemd.env`,
through `EnvironmentFile=`. It is aligned by the same rule while it exists: a rotated
token reached `.env` and stayed stale there, so the gateway process kept answering
with the old value, and a newly mounted server's token was missing entirely.
"""
from __future__ import annotations

import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.openclaw.setup_service_env import (  # noqa: E402 - sys.path shim for cloned checkout
    SERVICE_ENV_COPY,
    write_gateway_service_env,
)


@dataclass
class FakeSetup:
    """The two fields the writer reads, and the words it says."""

    state_dir: Path
    env: dict[str, str]
    config_path: Path
    said: list[str]

    def out(self, line: str) -> None:
        self.said.append(line)


def _setup(tmp_path: Path, env: dict[str, str]) -> FakeSetup:
    state = tmp_path / ".openclaw"
    state.mkdir()
    config = state / "openclaw.json"
    config.write_text("{}", encoding="utf-8")
    said: list[str] = []
    return FakeSetup(state_dir=state, env=env, config_path=config, said=said)


def test_a_rotated_secret_replaces_the_old_value(tmp_path):
    setup = _setup(tmp_path, {"GITHUB_PERSONAL_ACCESS_TOKEN": "nuovo-valore"})
    (setup.state_dir / ".env").write_text(
        'GITHUB_PERSONAL_ACCESS_TOKEN="vecchio-valore"\nALTRA="resta"\n', encoding="utf-8"
    )

    write_gateway_service_env(setup, {"mcp": {"servers": {"x": {"env": {"TOKEN": "${GITHUB_PERSONAL_ACCESS_TOKEN}"}}}}})

    written = (setup.state_dir / ".env").read_text(encoding="utf-8")
    assert "nuovo-valore" in written
    assert "vecchio-valore" not in written
    # A key the engine does not reference is the person's and stays as it was.
    assert 'ALTRA="resta"' in written


def test_the_value_never_reaches_the_output(tmp_path):
    setup = _setup(tmp_path, {"SECRET_TOKEN": "valore-che-non-si-stampa"})

    write_gateway_service_env(setup, {"mcp": {"servers": {"x": {"env": {"TOKEN": "${SECRET_TOKEN}"}}}}})

    assert all("valore-che-non-si-stampa" not in line for line in setup.said)
    assert (setup.state_dir / ".env").is_file()


def test_a_variable_every_process_has_is_never_frozen_in_the_file(tmp_path):
    """The HTTP bridge cites `${PATH}` and `${HOME}`: their setup-time values must not
    land in `.env`, where they would override the real ones on every later start."""
    import json

    setup = _setup(tmp_path, {"PATH": "/setup/shell/bin", "HOME": str(tmp_path / "setup-home"), "TOOL_TOKEN": "secret"})
    setup.config_path.write_text(json.dumps({"mcp": {"servers": {"tool": {
        "command": "node", "args": ["bridge.mjs"],
        "env": {"PATH": "${PATH}", "HOME": "${HOME}", "TOOL_TOKEN": "${TOOL_TOKEN}"},
    }}}}), encoding="utf-8")

    written = write_gateway_service_env(setup, {}).read_text(encoding="utf-8")

    assert 'TOOL_TOKEN="secret"' in written
    assert "PATH=" not in written and "HOME=" not in written


#: One agent on Claude Code's CLI: it signs in through the CLI, so nothing reads the key.
CLAUDE_CLI_CONFIG = {
    "agents": {
        "defaults": {"models": {"anthropic/claude-opus-5-5": {"agentRuntime": {"id": "claude-cli"}}}},
        "entries": {"capo": {"model": "anthropic/claude-opus-5-5"}},
    },
}

#: The second file the generated systemd unit loads through `EnvironmentFile=`.
COPY = "gateway.systemd.env"


def test_the_systemd_copy_is_aligned_by_the_same_rule_as_the_env_file(tmp_path):
    """A stale token in the copy reaches the gateway process, not only the file beside it."""
    setup = _setup(tmp_path, {
        "GITHUB_PERSONAL_ACCESS_TOKEN": "nuovo-valore",
        "FINANCE_AIS_MCP_TOKEN_NEXGEN": "token-del-server-nuovo",
    })
    copy = setup.state_dir / COPY
    copy.write_text(
        'GITHUB_PERSONAL_ACCESS_TOKEN="vecchio-valore"\nALTRA="resta"\n', encoding="utf-8"
    )

    write_gateway_service_env(setup, {"mcp": {"servers": {"x": {"env": {
        "TOKEN": "${GITHUB_PERSONAL_ACCESS_TOKEN}",
        "FINANCE_TOKEN": "${FINANCE_AIS_MCP_TOKEN_NEXGEN}",
    }}}}})

    written = copy.read_text(encoding="utf-8")
    assert 'GITHUB_PERSONAL_ACCESS_TOKEN="nuovo-valore"' in written
    assert "vecchio-valore" not in written
    # A line a person wrote is not the engine's, here either.
    assert 'ALTRA="resta"' in written
    # A newly referenced key reaches the copy, so the unit's gateway sees it too.
    assert 'FINANCE_AIS_MCP_TOKEN_NEXGEN="token-del-server-nuovo"' in written
    assert SERVICE_ENV_COPY == COPY
    if os.name != "nt":  # mode bits are a POSIX concept
        assert stat.S_IMODE(copy.stat().st_mode) == 0o600
    assert any(str(copy) in line for line in setup.said), setup.said
    assert all("nuovo-valore" not in line and "token-del-server-nuovo" not in line for line in setup.said)


def test_a_missing_systemd_copy_is_never_created(tmp_path):
    """A gateway this unit does not manage must not grow a stray secrets file."""
    setup = _setup(tmp_path, {"GITHUB_PERSONAL_ACCESS_TOKEN": "nuovo-valore"})

    write_gateway_service_env(setup, {"mcp": {"servers": {"x": {"env": {
        "TOKEN": "${GITHUB_PERSONAL_ACCESS_TOKEN}",
    }}}}})

    assert not (setup.state_dir / COPY).exists()
    assert (setup.state_dir / ".env").is_file()


def test_a_retired_key_is_removed_from_the_systemd_copy_too(tmp_path):
    """A clean `.env` with the key still in the copy keeps the catalogue failing."""
    setup = _setup(tmp_path, {"ANTHROPIC_API_KEY": "sk-ant-workspace-scoped"})
    copy = setup.state_dir / COPY
    copy.write_text('ANTHROPIC_API_KEY="sk-ant-workspace-scoped"\nOTHER_SECRET="resta"\n', encoding="utf-8")

    write_gateway_service_env(setup, CLAUDE_CLI_CONFIG)

    written = copy.read_text(encoding="utf-8")
    assert "ANTHROPIC_API_KEY" not in written
    assert 'OTHER_SECRET="resta"' in written
    assert all("sk-ant-workspace-scoped" not in line for line in setup.said)
