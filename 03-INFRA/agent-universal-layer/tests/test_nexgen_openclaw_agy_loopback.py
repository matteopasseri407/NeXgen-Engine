"""An agent on `agy` reads its connectors once: from `agy`'s file, not also from OpenClaw.

`google-antigravity-cli` publishes OpenClaw's loopback server -- every connector mounted in
OpenClaw, `alwaysLoad` -- inside the MCP configuration `agy` reads, for the length of every
turn, with the tools prefixed `openclaw__`. The engine writes those same connectors directly
into that file (`mcp_render.antigravity`), so an agent on `agy` sees each of them twice: the
943k-token shape the engine already removed for Claude Code. The deny this suite asks for is
the same one, decided the same way -- on the document the write leaves -- and gated on the one
fact that separates `agy` from Claude Code: the engine writes `agy`'s file only where the
renderer ran, so the deny is written only where that file exists.

Five promises, one per test:

- an `agy` agent is denied every connector, and none of OpenClaw's own tools;
- an agent that only the file puts on `agy`, through `agents.defaults.models`, is denied too;
- a machine with no `agy` file is written exactly as before;
- the doctor names an `agy` agent still on the loopback, and passes once a setup has written it;
- a machine with neither a `claude-cli` nor an `agy` agent is written exactly as before.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from test_nexgen_openclaw_setup import ENGINE_ROOT, Machine, detection, session_env  # noqa: E402 - the fake machine this suite already drives

from nexgen_core import i18n  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.checks.claude_loopback_checks import check_claude_loopback  # noqa: E402
from nexgen_core.openclaw import claude_loopback, setup_write  # noqa: E402
from nexgen_core.openclaw.claude_cli import claude_cli_agents  # noqa: E402
from nexgen_core.openclaw.claude_loopback import patterns as deny_patterns  # noqa: E402
from nexgen_core.openclaw.detect import Detection, Installation, LocalModels, Login  # noqa: E402
from nexgen_core.openclaw.native_connectors import agy_agents, denied_agents  # noqa: E402
from nexgen_core.openclaw.plan import build_plan  # noqa: E402
from nexgen_core.paths import antigravity_config, openclaw_config  # noqa: E402
from nexgen_core.report import Severity  # noqa: E402
from nexgen_core.renderer import McpRenderer  # noqa: E402

#: Two connectors the manifest mounts in every CLI, so the deny has something to name.
MANIFEST = """schema_version: 1
servers:
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

#: The connectors the test's manifest carries: what every native agent must deny.
SERVERS = ("vault-library", "n8n-mcp")

#: An `agy` model: the provider id is the runtime id, the shape `models.list` reports.
AGY_REF = "google-antigravity-cli/gemini-3.8-flash"
AGY_RUNTIME = "google-antigravity-cli"
GO_REF = "opencode-go/deepseek-v4.1-flash"


@pytest.fixture(autouse=True)
def _english_and_no_sockets(monkeypatch):
    """English output to assert on, no plugin deposited into a fixture's state dir, no socket."""
    i18n.set_language("en")
    monkeypatch.setattr("nexgen_core.openclaw.doctor_runtime.default_connect", lambda host, port, timeout: False)

    def _fake_plugin_step(setup):
        setup.out(i18n.t("The engine plugin is deposited and enabled."))
        return True

    monkeypatch.setattr("nexgen_core.openclaw.setup.plugin_step", _fake_plugin_step)
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


def _renderer(tmp_path: Path, home: Path) -> McpRenderer:
    """A renderer over a synthetic vault, so the manifest is what these tests say it is."""
    vault = tmp_path / "vault"
    path = vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(MANIFEST, encoding="utf-8")
    return McpRenderer(vault_data=vault, engine_root=tmp_path / "engine", home=home)


def _detected(config_path: Path) -> Detection:
    """A machine that has OpenClaw, the Go key and no Claude or Codex login of its own."""
    return Detection(
        openclaw=Installation(installed=True, version="2026.10.6", config_path=str(config_path), config_present=True),
        claude=Login(binary=False),
        codex=Login(binary=False),
        opencode_go=True,
        local=LocalModels(),
        n8n=True,
    )


def _put(home: Path, config: dict) -> Path:
    """The file in force, as a write of it would leave it."""
    path = openclaw_config(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(setup_write.render(config), encoding="utf-8")
    return path


def _agy_file(renderer: McpRenderer) -> Path:
    """`agy`'s own configuration, written by the renderer `nexgen sync` uses."""
    assert renderer.render_antigravity(write=True)[0] is True
    path = antigravity_config(renderer.home)
    assert path.is_file(), "the renderer wrote no agy configuration"
    return path


def _agy_agent_file() -> dict:
    """A gateway whose one `agy` agent names its model directly, and one connector in force."""
    return {
        "agents": {
            "entries": {
                "gemini": {"name": "gemini", "model": AGY_REF},
            },
        },
        "mcp": {"servers": {"vault-library": {"command": "node", "args": ["vault.mjs"]}}},
    }


def _agy_default_file() -> dict:
    """A gateway where the default model pins every agent to `agy`, and an agent names none.

    `carriera` is not in the roster a setup builds, so it is the file -- and only the file --
    that puts it on `agy`; the doctor reads that same file.
    """
    return {
        "agents": {
            "defaults": {
                "model": AGY_REF,
                "models": {AGY_REF: {"agentRuntime": {"id": AGY_RUNTIME}}},
            },
            "entries": {
                "carriera": {"name": "carriera", "workspace": "/home/person/.openclaw/workspace-carriera"},
            },
        },
        "mcp": {"servers": {"vault-library": {"command": "node", "args": ["vault.mjs"]}}},
    }


def _go_only_file() -> dict:
    """A machine whose every agent names a Go model: nothing here can reach `agy` or Claude Code."""
    return {
        "agents": {
            "entries": {
                "main": {"name": "main", "model": GO_REF},
                "automazioni": {"name": "automazioni", "model": GO_REF},
            },
        },
        "mcp": {"servers": {"vault-library": {"command": "node", "args": ["vault.mjs"]}}},
    }


def _plan(home: Path, renderer: McpRenderer):
    """The plan a setup on this machine would print and apply."""
    return build_plan(
        detected=_detected(openclaw_config(home)), home=home, env={},
        vault_data=renderer.vault_data, engine_root=renderer.engine_root,
    )


def _written(plan, path: Path) -> dict:
    """What `write_step` puts on disk: the merge of the proposal inside the file in force."""
    document, _generated = setup_write.with_token(
        setup_write.merged_document(plan.current, plan.config), lambda: "generated-for-this-test",
    )
    path.write_text(setup_write.render(document), encoding="utf-8")
    return document


def _deny(config: dict, agent_id: str) -> set[str]:
    """The deny patterns one agent's entry carries, as the doctor reads them."""
    entry = (config.get("agents") or {}).get("entries", {}).get(agent_id) or {}
    deny = (entry.get("tools") or {}).get("deny")
    return {str(item) for item in deny} if isinstance(deny, list) else set()


def test_an_agy_agent_keeps_the_connectors_off_the_loopback(tmp_path: Path) -> None:
    """The deny covers every connector, and never OpenClaw's own tools."""
    home = _home(tmp_path)
    renderer = _renderer(tmp_path, home)
    path = _put(home, _agy_agent_file())
    _agy_file(renderer)

    document = _written(_plan(home, renderer), path)

    assert _deny(document, "gemini") == set(deny_patterns(SERVERS)), (
        "the agent on agy keeps every connector, and no more"
    )
    assert "openclaw__*" not in _deny(document, "gemini"), "OpenClaw's own tools stay reachable"
    assert not [item for item in document["agents"]["entries"]["gemini"].get("tools", {}) if item != "deny"]


def test_an_agent_the_file_puts_on_agy_through_the_defaults_is_denied_too(tmp_path: Path) -> None:
    """The question is asked of the merged document: `carriera` inherits `agy` from the file."""
    home = _home(tmp_path)
    renderer = _renderer(tmp_path, home)
    path = _put(home, _agy_default_file())
    _agy_file(renderer)

    plan = _plan(home, renderer)
    document = _written(plan, path)

    assert set(plan.config["agents"]["entries"]["carriera"]) == {"tools"}, (
        "only the deny pass writes this agent: the proposal does not declare it"
    )
    assert document["agents"]["entries"]["carriera"]["workspace"].endswith("workspace-carriera"), (
        "the merge puts the deny inside the entry the file already has"
    )
    assert claude_cli_agents(document) == []
    assert _deny(document, "carriera") == set(deny_patterns(SERVERS)), (
        "an agent only the file puts on agy is still an agent the file runs"
    )


def test_a_machine_with_no_agy_file_is_written_exactly_as_before(tmp_path: Path) -> None:
    """The deny waits for the file that carries the connectors: without it they are lost."""
    home = _home(tmp_path)
    renderer = _renderer(tmp_path, home)
    path = _put(home, _agy_agent_file())
    assert not antigravity_config(home).exists()

    plan = _plan(home, renderer)
    document = _written(plan, path)

    assert denied_agents(plan.config, home) == []
    for agent_id, entry in document["agents"]["entries"].items():
        assert "deny" not in (entry.get("tools") or {}), agent_id
    assert "__*" not in setup_write.render(document), "no connector pattern reached the file"
    assert not [item for item in (*plan.added, *plan.changed) if item.endswith(".deny")]


def test_the_doctor_names_an_agy_agent_still_on_the_loopback_and_passes_after_a_setup(tmp_path: Path) -> None:
    """The whole round trip: the fault is named, the setup writes the deny, the fault is gone."""
    machine = Machine(tmp_path)
    renderer = _renderer(tmp_path, machine.home)
    machine.config.parent.mkdir(parents=True, exist_ok=True)
    machine.config.write_text(setup_write.render(_agy_agent_file()), encoding="utf-8")
    _agy_file(renderer)

    before = check_claude_loopback(machine.home, machine.vault, ENGINE_ROOT)
    assert before is not None, "an agent runs on agy here, so the doctor has an answer"
    assert before.severity == Severity.BROKEN
    assert "gemini" in before.message, before.message
    assert "vault-library" in before.message, before.message

    code = machine.run(detected=detection(machine.config), env={"PATH": "/fake/bin", **session_env(sys.platform)})

    assert code == 0, machine.output()
    assert agy_agents(machine.config_json()) == ["gemini"], "the setup did not move it off agy"
    assert _deny(machine.config_json(), "gemini") == set(deny_patterns(SERVERS))
    after = check_claude_loopback(machine.home, machine.vault, ENGINE_ROOT)
    assert after is not None
    assert after.severity == Severity.OK, after.message


def test_a_machine_with_no_native_agent_is_written_exactly_as_before(tmp_path: Path) -> None:
    """No `claude-cli` and no `agy` agent: `agy`'s file is there, and nothing is asked of it."""
    home = _home(tmp_path)
    renderer = _renderer(tmp_path, home)
    path = _put(home, _go_only_file())
    _agy_file(renderer)

    plan = _plan(home, renderer)
    document = _written(plan, path)

    assert denied_agents(plan.config, home) == []
    assert {"main", "automazioni"} <= set(json.loads(setup_write.render(document))["agents"]["entries"])
    for agent_id, entry in document["agents"]["entries"].items():
        assert "deny" not in (entry.get("tools") or {}), agent_id
    assert "__*" not in setup_write.render(document)
    assert check_claude_loopback(home, renderer.vault_data, renderer.engine_root) is None


def test_the_doctor_names_the_agents_of_both_clis(tmp_path: Path) -> None:
    """One fault, two CLIs: the line that reports it speaks for both."""
    home = _home(tmp_path)
    renderer = _renderer(tmp_path, home)
    _put(home, {
        "agents": {"entries": {
            "claude-agent": {
                "model": "anthropic/claude-opus-5-5",
                "models": {"anthropic/claude-opus-5-5": {"agentRuntime": {"id": "claude-cli"}}},
            },
            "gemini": {"model": AGY_REF},
        }},
        "mcp": {"servers": {"vault-library": {"command": "node", "args": ["vault.mjs"]}}},
    })
    _agy_file(renderer)
    assert renderer.render_claude(write=True)[0] is True

    outcome = check_claude_loopback(home, renderer.vault_data, renderer.engine_root)

    assert outcome is not None
    assert outcome.severity == Severity.BROKEN
    assert "claude-agent" in outcome.message, outcome.message
    assert "gemini" in outcome.message, outcome.message
    assert "Agents run on CLIs that carry their own connectors" in outcome.message, outcome.message


def test_the_loopback_patterns_still_leave_openclaw_alone() -> None:
    """The one name the deny must never carry: OpenClaw's own tools live there."""
    assert claude_loopback.pattern_for("openclaw") is None
    assert deny_patterns([*SERVERS, "openclaw"]) == ["n8n-mcp__*", "vault-library__*"]
