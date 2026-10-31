"""An OpenClaw agent on Claude Code's CLI reads its connectors from Claude Code itself.

The behaviour under test is the one that cost 943k tokens per call: OpenClaw hands a
`claude-cli` agent an MCP server of its own marked `alwaysLoad: true`, carrying every
tool the agent has, connectors included. These tests pin the three parts of the answer
-- which agents are on that runtime, the deny that keeps connectors off the loopback,
and the native Claude Code configuration that carries them instead -- and pin the
silence: a machine without OpenClaw, or with no agent on `claude-cli`, keeps exactly
the configuration it had.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import i18n  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.checks.claude_loopback_checks import check_claude_loopback  # noqa: E402
from nexgen_core.openclaw.claude_cli import claude_cli_agents, is_claude_cli, runtime_id  # noqa: E402
from nexgen_core.openclaw.claude_loopback import apply as apply_loopback  # noqa: E402
from nexgen_core.openclaw.claude_native import ensure as ensure_native  # noqa: E402
from nexgen_core.paths import claude_config, openclaw_config  # noqa: E402
from nexgen_core.report import Severity  # noqa: E402
from nexgen_core.renderer import McpRenderer  # noqa: E402

#: Two connectors, one of them with a tool filter, mounted directly by both CLIs.
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

CLAUDE_REF = "anthropic/claude-opus-5-5"
GO_REF = "opencode-go/deepseek-v4.1-flash"


@pytest.fixture(autouse=True)
def _english():
    """These lines are English by construction."""
    i18n.set_language("en")
    yield
    i18n.set_language(None)


@pytest.fixture(autouse=True)
def _no_openclaw_env(monkeypatch):
    monkeypatch.delenv("OPENCLAW_CONFIG_PATH", raising=False)
    monkeypatch.delenv("OPENCLAW_STATE_DIR", raising=False)


def _config(
    *,
    claude_ref: str = CLAUDE_REF,
    go_ref: str = GO_REF,
    servers: tuple[str, ...] = ("vault-library", "n8n-mcp"),
    gateway: bool = True,
) -> dict:
    """The shape the engine writes: the pin on `agents.defaults.models`, connectors beside it.

    The engine's `lazy-mcp` entry is the road a `claude-cli` agent keeps once the
    connectors are denied on the loopback, so it is part of the shape by default.
    """
    mounted = {name: {"command": "node", "args": [f"{name}.mjs"]} for name in servers}
    if gateway:
        mounted["lazy-mcp"] = {
            "command": "python3",
            "args": ["lazy-mcp.py"],
            "env": {"LAZY_MCP_CLI": "openclaw"},
        }
    return {
        "agents": {
            "defaults": {"models": {claude_ref: {"agentRuntime": {"id": "claude-cli"}}}},
            "entries": {
                "capo": {
                    "model": claude_ref,
                    "tools": {
                        "alsoAllow": ["nexgen_sup_start", "nexgen_sup_unit"],
                        "agentToAgent": {"send": ["main"]},
                    },
                },
                "main": {"model": go_ref},
            },
        },
        "mcp": {"servers": mounted},
    }


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    return home


def _write_openclaw(home: Path, config: dict) -> Path:
    path = openclaw_config(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return path


def _renderer(tmp_path: Path, home: Path, manifest: str = MANIFEST) -> McpRenderer:
    """A renderer over a synthetic vault, so the manifest is what these tests say it is."""
    vault = tmp_path / "vault"
    path = vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(manifest, encoding="utf-8")
    return McpRenderer(vault_data=vault, engine_root=tmp_path / "engine", home=home)


# --- A: the one function that says "this agent runs on claude-cli" ------------------


def test_an_anthropic_agent_pinned_to_claude_cli_is_recognised() -> None:
    config = _config()
    assert is_claude_cli(config, "capo") is True
    assert claude_cli_agents(config) == ["capo"]


def test_a_non_anthropic_agent_is_not_on_claude_cli() -> None:
    config = _config()
    assert is_claude_cli(config, "main") is False
    assert claude_cli_agents(config) == ["capo"]


def test_the_pin_may_sit_on_the_provider_model_entry_instead_of_the_defaults() -> None:
    """OpenClaw resolves the same pin from `models.providers`; this engine reads it there too."""
    config = {
        "agents": {"entries": {"capo": {"model": CLAUDE_REF}}},
        "models": {"providers": {"anthropic": {"models": [
            {"id": "claude-opus-5-5", "name": "Claude Opus 5.5", "agentRuntime": {"id": "claude-cli"}},
        ]}}},
    }
    assert runtime_id(config, "capo", CLAUDE_REF) == "claude-cli"
    assert is_claude_cli(config, "capo") is True


def test_an_anthropic_agent_on_the_embedded_runtime_is_not_claude_cli() -> None:
    config = _config()
    config["agents"]["defaults"]["models"] = {CLAUDE_REF: {"agentRuntime": {"id": "openclaw"}}}
    assert claude_cli_agents(config) == []


# --- B: the connectors stay off the loopback server ---------------------------------


def test_every_connector_is_denied_on_the_loopback_for_a_claude_cli_agent() -> None:
    config = apply_loopback(_config(), ["vault-library", "n8n-mcp"])
    assert config["agents"]["entries"]["capo"]["tools"]["deny"] == ["n8n-mcp__*", "vault-library__*"]


def test_an_agent_on_another_runtime_gets_no_deny_at_all() -> None:
    config = apply_loopback(_config(), ["vault-library", "n8n-mcp"])
    assert "tools" not in config["agents"]["entries"]["main"]


def test_the_supervisor_tool_allowlist_survives_the_deny() -> None:
    """`alsoAllow` is the one list that lets `capo` drive the supervision: it is never touched."""
    before = _config()
    after = apply_loopback(before, ["vault-library"])
    assert after["agents"]["entries"]["capo"]["tools"]["alsoAllow"] == ["nexgen_sup_start", "nexgen_sup_unit"]


def test_a_person_allow_list_survives_the_deny() -> None:
    config = _config()
    config["agents"]["entries"]["capo"]["tools"]["allow"] = ["Read", "Bash"]
    after = apply_loopback(config, ["vault-library"])
    assert after["agents"]["entries"]["capo"]["tools"]["allow"] == ["Read", "Bash"]


def test_an_existing_deny_is_merged_sorted_and_without_duplicates() -> None:
    config = _config()
    config["agents"]["entries"]["capo"]["tools"]["deny"] = ["write", "vault-library__*"]
    after = apply_loopback(config, ["vault-library", "n8n-mcp"])
    assert after["agents"]["entries"]["capo"]["tools"]["deny"] == [
        "n8n-mcp__*", "vault-library__*", "write",
    ]


def test_running_the_deny_twice_changes_nothing_the_second_time() -> None:
    once = apply_loopback(_config(), ["vault-library", "n8n-mcp"])
    twice = apply_loopback(once, ["vault-library", "n8n-mcp"])
    assert twice == once


def test_the_gateway_is_never_denied_because_it_is_the_road_that_remains() -> None:
    """The connectors are denied on the loopback; the gateway that serves them is not."""
    config = _config()
    after = apply_loopback(config, list(config["mcp"]["servers"]))
    deny = after["agents"]["entries"]["capo"]["tools"]["deny"]
    assert "lazy-mcp__*" not in deny
    assert {"vault-library__*", "n8n-mcp__*"} <= set(deny)


def test_a_gateway_without_connectors_is_left_untouched() -> None:
    config = _config(servers=())
    assert apply_loopback(config, []) is config


# --- B through the plan: the proposal is where the deny is written ------------------


def _detected(config_path: Path, *, claude: bool = True) -> object:
    """A machine that has OpenClaw and, when asked, a Claude login."""
    from nexgen_core.openclaw.detect import Detection, Installation, LocalModels, Login

    return Detection(
        openclaw=Installation(installed=True, version="2026.10.6", config_path=str(config_path), config_present=True),
        claude=Login(binary=claude, login=claude),
        codex=Login(binary=not claude, login=not claude),
        opencode_go=True,
        local=LocalModels(),
        n8n=True,
    )


def test_the_plan_denies_every_connector_it_also_renders(tmp_path: Path) -> None:
    """The connectors the next sync mounts are in the manifest and not yet in the file."""
    from nexgen_core.openclaw.plan import build_plan

    home = _home(tmp_path)
    renderer = _renderer(tmp_path, home)
    path = _write_openclaw(home, {"mcp": {"servers": {"hand-added": {"command": "node", "args": ["x.mjs"]}}}})

    plan = build_plan(
        detected=_detected(path), home=home, env={},
        vault_data=renderer.vault_data, engine_root=renderer.engine_root,
    )

    assert plan.config["agents"]["entries"]["capo"]["tools"]["deny"] == [
        "hand-added__*", "n8n-mcp__*", "vault-library__*",
    ]
    assert "deny" not in plan.config["agents"]["entries"]["main"].get("tools", {})


def test_the_plan_writes_no_deny_when_no_agent_runs_on_claude_cli(tmp_path: Path) -> None:
    from nexgen_core.openclaw.plan import build_plan

    home = _home(tmp_path)
    renderer = _renderer(tmp_path, home)
    path = _write_openclaw(home, {"mcp": {"servers": {"vault-library": {}}}})

    plan = build_plan(
        detected=_detected(path, claude=False), home=home, env={},
        vault_data=renderer.vault_data, engine_root=renderer.engine_root,
    )

    assert all("deny" not in (entry.get("tools") or {}) for entry in plan.config["agents"]["entries"].values())



# --- C: Claude Code's own configuration carries the connectors ----------------------


def test_the_native_config_is_rendered_and_says_where_the_connectors_come_from(tmp_path: Path) -> None:
    home = _home(tmp_path)
    renderer = _renderer(tmp_path, home)
    lines: list[str] = []
    assert ensure_native(_config(), home=home, vault_data=renderer.vault_data,
                         engine_root=renderer.engine_root, out=lines.append) is True

    written = json.loads(claude_config(home).read_text(encoding="utf-8"))["mcpServers"]
    assert set(written) == {"vault-library", "n8n-mcp"}
    assert lines[0] == "The connectors of capo reach it through OpenClaw's gateway, not the loopback server."
    assert lines[-1] == "Claude configuration updated"


def test_the_native_config_carries_no_always_load(tmp_path: Path) -> None:
    """`alwaysLoad` is the switch that put every schema in every prompt: the engine writes none."""
    home = _home(tmp_path)
    renderer = _renderer(tmp_path, home)
    ensure_native(_config(), home=home, vault_data=renderer.vault_data, engine_root=renderer.engine_root,
                  out=lambda _line: None)

    text = claude_config(home).read_text(encoding="utf-8")
    assert "alwaysLoad" not in text


# --- Nothing changes for a machine that does not run this ---------------------------


def test_a_gateway_with_no_claude_cli_agent_changes_nothing(tmp_path: Path) -> None:
    home = _home(tmp_path)
    renderer = _renderer(tmp_path, home)
    config = _config()
    config["agents"]["defaults"]["models"] = {}
    lines: list[str] = []

    assert apply_loopback(config, ["vault-library", "n8n-mcp"]) is config
    assert ensure_native(config, home=home, vault_data=renderer.vault_data,
                         engine_root=renderer.engine_root, out=lines.append) is True
    assert lines == []
    assert not claude_config(home).exists()


def test_a_machine_without_openclaw_renders_claude_exactly_as_before(tmp_path: Path) -> None:
    """No OpenClaw file at all: the Claude Code render is the only thing that runs, unchanged."""
    home = _home(tmp_path)
    renderer = _renderer(tmp_path, home)

    assert renderer.render_claude(write=True)[0] is True
    before = claude_config(home).read_bytes()
    # The shape this engine has always written for these two servers, as a literal: the
    # renderer is not part of this change, and a machine without OpenClaw must not see one.
    assert json.loads(before.decode("utf-8"))["mcpServers"] == {
        "n8n-mcp": {"type": "stdio", "command": "node", "args": ["n8n.mjs"], "env": {}},
        "vault-library": {"type": "stdio", "command": "node", "args": ["vault.mjs"], "env": {}},
    }

    # The check is silent, and nothing else in this change writes into a machine that
    # does not run OpenClaw.
    assert check_claude_loopback(home, renderer.vault_data, renderer.engine_root) is None
    assert renderer.render_claude(write=True)[0] is True
    assert claude_config(home).read_bytes() == before
    assert not openclaw_config(home).exists()


# --- E: the doctor -----------------------------------------------------------------

def _healthy(tmp_path: Path) -> tuple[Path, McpRenderer]:
    """A gateway whose `claude-cli` agent is where this change puts it: denied and native."""
    home = _home(tmp_path)
    renderer = _renderer(tmp_path, home)
    config = apply_loopback(_config(), ["vault-library", "n8n-mcp"])
    _write_openclaw(home, config)
    ensure_native(config, home=home, vault_data=renderer.vault_data, engine_root=renderer.engine_root,
                  out=lambda _line: None)
    return home, renderer


def test_the_doctor_is_silent_without_openclaw(tmp_path: Path) -> None:
    home = _home(tmp_path)
    renderer = _renderer(tmp_path, home)
    assert check_claude_loopback(home, renderer.vault_data, renderer.engine_root) is None


def test_the_doctor_is_silent_when_no_agent_runs_on_claude_cli(tmp_path: Path) -> None:
    home = _home(tmp_path)
    renderer = _renderer(tmp_path, home)
    config = _config()
    config["agents"]["defaults"]["models"] = {}
    _write_openclaw(home, apply_loopback(config, ["vault-library", "n8n-mcp"]))
    assert check_claude_loopback(home, renderer.vault_data, renderer.engine_root) is None


def test_the_doctor_does_not_demand_a_gateway_when_no_connector_is_mounted(tmp_path: Path) -> None:
    """Nothing is denied, so there is nothing behind the gateway to reach."""
    home = _home(tmp_path)
    renderer = _renderer(tmp_path, home)
    _write_openclaw(home, _config(servers=(), gateway=False))

    outcome = check_claude_loopback(home, renderer.vault_data, renderer.engine_root)
    assert outcome is not None
    assert outcome.severity == Severity.OK


def test_the_doctor_passes_a_configuration_this_change_writes(tmp_path: Path) -> None:
    home, renderer = _healthy(tmp_path)
    outcome = check_claude_loopback(home, renderer.vault_data, renderer.engine_root)
    assert outcome is not None
    assert outcome.severity == Severity.OK
    assert outcome.id == "openclaw.claude_loopback"


def test_the_doctor_reports_a_connector_still_on_the_loopback(tmp_path: Path) -> None:
    home, renderer = _healthy(tmp_path)
    config = json.loads(openclaw_config(home).read_text(encoding="utf-8"))
    config["agents"]["entries"]["capo"]["tools"]["deny"] = ["vault-library__*"]
    _write_openclaw(home, config)

    outcome = check_claude_loopback(home, renderer.vault_data, renderer.engine_root)
    assert outcome is not None
    assert outcome.severity == Severity.BROKEN
    assert "n8n-mcp" in outcome.message
    assert "nexgen openclaw setup" in (outcome.action or "")


def test_the_doctor_reports_a_missing_gateway(tmp_path: Path) -> None:
    """Without the gateway in OpenClaw, a `claude-cli` agent has no road to its connectors at all."""
    home, renderer = _healthy(tmp_path)
    config = json.loads(openclaw_config(home).read_text(encoding="utf-8"))
    config["mcp"]["servers"].pop("lazy-mcp")
    _write_openclaw(home, config)

    outcome = check_claude_loopback(home, renderer.vault_data, renderer.engine_root)
    assert outcome is not None
    assert outcome.severity == Severity.BROKEN
    assert "lazy-mcp" in outcome.message
    assert "nexgen openclaw setup" in (outcome.action or "")


def test_the_doctor_reports_a_gateway_that_is_not_the_engines_one(tmp_path: Path) -> None:
    """A hand-written proxy under the same name is not the engine's gateway, and proves nothing."""
    home, renderer = _healthy(tmp_path)
    config = json.loads(openclaw_config(home).read_text(encoding="utf-8"))
    config["mcp"]["servers"]["lazy-mcp"] = {"command": "node", "args": ["custom-proxy.js"]}
    _write_openclaw(home, config)

    outcome = check_claude_loopback(home, renderer.vault_data, renderer.engine_root)
    assert outcome is not None
    assert outcome.severity == Severity.BROKEN
    assert "lazy-mcp" in outcome.message
    assert "Claude Code's own configuration" not in outcome.message


def test_a_connector_the_manifest_keeps_off_claude_is_still_denied_and_reachable(tmp_path: Path) -> None:
    """Every connector the OpenClaw file mounts belongs off the loopback, whatever target it names:
    the gateway makes it reachable, so the deny costs the agent nothing."""
    manifest = """schema_version: 1
servers:
  vault-library:
    exposure: eager
    transport: stdio
    command: node
    args: ["vault.mjs"]
  openclaw-only:
    exposure: eager
    targets: [openclaw]
    transport: stdio
    command: node
    args: ["own.mjs"]
"""
    home = _home(tmp_path)
    renderer = _renderer(tmp_path, home, manifest)
    config = apply_loopback(_config(servers=("vault-library", "openclaw-only")), ["vault-library", "openclaw-only"])
    _write_openclaw(home, config)
    ensure_native(config, home=home, vault_data=renderer.vault_data, engine_root=renderer.engine_root,
                  out=lambda _line: None)

    deny = json.loads(openclaw_config(home).read_text(encoding="utf-8"))["agents"]["entries"]["capo"]["tools"]["deny"]
    assert "openclaw-only__*" in deny
    outcome = check_claude_loopback(home, renderer.vault_data, renderer.engine_root)
    assert outcome is not None
    assert outcome.severity == Severity.OK
