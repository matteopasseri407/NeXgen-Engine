"""The steps that run after the write judge the document the write leaves, not the proposal.

`nexgen openclaw setup` writes its proposal merged inside the file in force, so keys the
engine does not propose -- `agents.defaults.models` above all, and the agents the file
still declares -- decide which agents run on `claude-cli`. Two steps ask that question
after the write, and both asked it of the proposal:

- the gateway's `.env` writer takes `ANTHROPIC_API_KEY` out only when an agent runs on
  Claude Code's CLI, and reads a provider key as a reference for every agent that does
  not. Asked of the proposal, the key stayed in `.env`, OpenClaw's live catalogue called
  `GET /v1/models` with it, every model came back unavailable, and the apps said they
  could not update the models;
- Claude Code's own configuration is rendered only when a `claude-cli` agent exists. Asked
  of the proposal, a gateway whose `claude-cli` agents all come from the file got no
  render: the setup took their connectors off the loopback and gave them nothing in
  exchange.

These three tests drive the real `run_setup` on a fake machine, so the call sites are the
ones that ship. The last one is the guard: a `${ANTHROPIC_API_KEY}` somebody wrote on
purpose is a reference, and the key stays.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from test_nexgen_openclaw_setup import ENGINE_ROOT, Machine, detection, session_env  # noqa: E402 - the fake machine this suite already drives

from nexgen_core import i18n  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.checks.claude_loopback_checks import check_claude_loopback  # noqa: E402
from nexgen_core.paths import claude_config  # noqa: E402
from nexgen_core.report import Severity  # noqa: E402

#: Two connectors mounted directly by both CLIs, plus the gateway a `claude-cli` agent reads them through.
MANIFEST = """schema_version: 1
servers:
  lazy-mcp:
    exposure: eager
    tier: core
    transport: stdio
    command: python3
    args: ["${AGENT_ENGINE_ROOT}/agent-universal-layer/mcp/lazy-mcp.py"]
    targets: [claude, codex, antigravity, opencode, openclaw]
  vault-library:
    exposure: eager
    transport: stdio
    command: node
    args: ["vault.mjs"]
  n8n-mcp:
    exposure: eager
    transport: stdio
    command: node
    args: ["n8n.mjs"]
"""

#: The server's shape: the squad names two agents on Anthropic models that the proposal
#: itself does not pin, so only the file in force says they run on Claude Code's CLI.
SQUAD = """agents:
  main:
    name: assistente
    model: anthropic/claude-sonnet-4
  marketing:
    name: marketing
    model: anthropic/claude-haiku-4
"""

SONNET = "anthropic/claude-sonnet-4"
HAIKU = "anthropic/claude-haiku-4"

#: The gateway's environment before the setup: the key at its old value, and a line that is the person's.
ENV_BEFORE = 'ANTHROPIC_API_KEY="a-key-nobody-wrote-here"\nALTRA="resta"\n'


@pytest.fixture(autouse=True)
def _english_and_no_sockets(monkeypatch):
    """English output, no plugin deposited into a fixture's state dir, no socket to the real port."""
    i18n.set_language("en")
    monkeypatch.setattr("nexgen_core.openclaw.doctor_runtime.default_connect", lambda host, port, timeout: False)

    def _fake_plugin_step(setup):
        setup.out(i18n.t("The engine plugin is deposited and enabled."))
        return True

    monkeypatch.setattr("nexgen_core.openclaw.setup.plugin_step", _fake_plugin_step)
    yield
    i18n.set_language(None)


def _manifest(vault: Path) -> None:
    path = vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(MANIFEST, encoding="utf-8")


def _carried_file(server_env: dict[str, str] | None = None) -> dict:
    """A file that pins both Anthropic models to `claude-cli` and declares one agent of its own."""
    server: dict = {"command": "node", "args": ["vault.mjs"]}
    if server_env:
        server["env"] = server_env
    return {
        "agents": {
            "defaults": {"models": {
                SONNET: {"agentRuntime": {"id": "claude-cli"}},
                HAIKU: {"agentRuntime": {"id": "claude-cli"}},
            }},
            # Not in the roster: the write leaves this agent where the file has it.
            "entries": {"carriera": {"name": "carriera", "model": SONNET}},
        },
        "mcp": {"servers": {"vault-library": server}},
    }


def _machine(tmp_path: Path, *, server_env: dict[str, str] | None = None) -> Machine:
    """A fake machine with the file in force, its squad, and a `.env` from an earlier run."""
    machine = Machine(tmp_path)
    _manifest(machine.vault)
    (machine.vault / "squad.yaml").write_text(SQUAD, encoding="utf-8")
    machine.config.parent.mkdir(parents=True, exist_ok=True)
    machine.config.write_text(json.dumps(_carried_file(server_env), indent=2) + "\n", encoding="utf-8")
    (machine.state_dir / ".env").write_text(ENV_BEFORE, encoding="utf-8")
    return machine


def _run(machine: Machine) -> str:
    """The whole setup, on a machine with no Claude login: the only `claude-cli` agents are the file's."""
    # A healthy session, so a Linux runner meets the systemd preflight as a desktop would.
    env = {"PATH": "/fake/bin", **session_env(sys.platform)}
    code = machine.run(detected=detection(machine.config, claude=False), env=env)
    assert code == 0, machine.output()
    return (machine.state_dir / ".env").read_text(encoding="utf-8")


def _agents_on_claude_cli(machine: Machine) -> list[str]:
    """Who runs on Claude Code's CLI in the file the setup wrote, read with the doctor's own answer."""
    from nexgen_core.openclaw.claude_cli import claude_cli_agents

    return claude_cli_agents(machine.config_json())


def test_a_carried_claude_cli_agent_takes_the_anthropic_key_out_of_the_gateway_env(tmp_path: Path) -> None:
    """The key is a reference only for an agent that does not run on Claude Code's CLI."""
    machine = _machine(tmp_path)

    env_text = _run(machine)

    assert _agents_on_claude_cli(machine) == ["carriera", "main", "marketing"]
    assert "ANTHROPIC_API_KEY" not in env_text, env_text
    assert 'ALTRA="resta"' in env_text, "a line the engine does not know is the person's"


def test_the_carried_claude_cli_agents_get_their_connectors_through_the_gateway(tmp_path: Path) -> None:
    """What the deny took off the loopback has to reach the agents somewhere: the gateway."""
    machine = _machine(tmp_path)

    _run(machine)

    native = json.loads(claude_config(machine.home).read_text(encoding="utf-8"))["mcpServers"]
    assert set(native) == {"lazy-mcp", "vault-library", "n8n-mcp"}
    gateway = machine.config_json()["mcp"]["servers"]["lazy-mcp"]
    assert gateway["env"]["LAZY_MCP_CLI"] == "openclaw"
    outcome = check_claude_loopback(machine.home, machine.vault, ENGINE_ROOT)
    assert outcome is not None, "three agents run on claude-cli here, so the doctor has an answer"
    assert outcome.severity == Severity.OK, outcome.message


def test_an_explicit_anthropic_reference_keeps_the_key_in_the_env(tmp_path: Path) -> None:
    """A `${ANTHROPIC_API_KEY}` somebody wrote is a reference the retirement must not override."""
    machine = _machine(tmp_path, server_env={"TOKEN": "${ANTHROPIC_API_KEY}"})

    env_text = _run(machine)

    assert _agents_on_claude_cli(machine) == ["carriera", "main", "marketing"]
    assert "ANTHROPIC_API_KEY" in env_text, env_text
    assert 'ALTRA="resta"' in env_text
