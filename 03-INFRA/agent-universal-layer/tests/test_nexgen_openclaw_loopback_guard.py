"""Observable tests for loopback guard and property invariants across connectors.

nexgen: one-job reason="prove whoever mounts a connector also denies it, across render, setup, and arbitrary connector names"
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import pytest  # noqa: E402
from nexgen_core.openclaw.loopback_guard import apply_loopback_guard, connector_names  # noqa: E402
from nexgen_core.paths import openclaw_config  # noqa: E402
from nexgen_core.renderer import McpRenderer  # noqa: E402


@pytest.fixture
def test_env(tmp_path: Path):
    """An isolated test environment with vault, engine root, and home."""
    home = tmp_path / "home"
    vault = tmp_path / "vault"
    engine = tmp_path / "engine"
    home.mkdir(parents=True)
    vault.mkdir(parents=True)
    engine.mkdir(parents=True)

    manifest_path = vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        """schema_version: 1
servers:
  vault-library:
    tier: core
    transport: stdio
    command: node
    args: ["/path/to/vault.js"]
""",
        encoding="utf-8",
    )
    renderer = McpRenderer(vault_data=vault, engine_root=engine, home=home)
    return home, vault, engine, renderer, manifest_path


def test_render_with_manifest_that_gains_one_server(test_env) -> None:
    """(a) Render with a manifest that gains one server writes the deny in the same render."""
    home, vault, engine, renderer, manifest_path = test_env
    cfg_file = openclaw_config(home)
    cfg_file.parent.mkdir(parents=True, exist_ok=True)

    initial_config = {
        "models": {
            "providers": {
                "anthropic": {
                    "agentRuntime": {"id": "claude-cli"},
                }
            }
        },
        "agents": {
            "entries": {
                "main": {
                    "model": "anthropic/claude-3-5-sonnet",
                    "agentRuntime": {"id": "claude-cli"},
                    "tools": {"deny": ["existing__*"]},
                }
            }
        },
        "mcp": {"servers": {}},
    }
    cfg_file.write_text(json.dumps(initial_config, indent=2), encoding="utf-8")

    # Sync render
    ok, msg = renderer.render_openclaw(write=True)
    assert ok is True

    written = json.loads(cfg_file.read_text(encoding="utf-8"))
    # The mounted server is in mcp.servers
    assert "vault-library" in written["mcp"]["servers"]

    # Both agent deny and provider deny carry the new pattern in that single render
    agent_deny = written["agents"]["entries"]["main"]["tools"]["deny"]
    assert "vault-library__*" in agent_deny
    assert "existing__*" in agent_deny

    prov_deny = written["tools"]["byProvider"]["anthropic"]["deny"]
    assert "vault-library__*" in prov_deny


def test_render_twice_changes_nothing(test_env) -> None:
    """(b) Calling render twice performs no modification on the second pass."""
    home, vault, engine, renderer, _ = test_env
    cfg_file = openclaw_config(home)
    cfg_file.parent.mkdir(parents=True, exist_ok=True)

    initial_config = {
        "models": {
            "providers": {
                "anthropic": {
                    "agentRuntime": {"id": "claude-cli"},
                }
            }
        },
        "agents": {
            "entries": {
                "main": {
                    "model": "anthropic/claude-3-5-sonnet",
                    "agentRuntime": {"id": "claude-cli"},
                }
            }
        },
        "mcp": {"servers": {}},
    }
    cfg_file.write_text(json.dumps(initial_config, indent=2), encoding="utf-8")

    # First render
    renderer.render_openclaw(write=True)
    mtime_after_first = cfg_file.stat().st_mtime_ns
    content_after_first = cfg_file.read_text(encoding="utf-8")

    # Second render
    renderer.render_openclaw(write=True)
    mtime_after_second = cfg_file.stat().st_mtime_ns
    content_after_second = cfg_file.read_text(encoding="utf-8")

    assert content_after_first == content_after_second
    assert mtime_after_first == mtime_after_second


def test_machine_with_no_cli_runtime_writes_no_deny(tmp_path: Path) -> None:
    """(c) A machine with no CLI runtime anywhere writes no deny at all."""
    home = tmp_path / "home"
    vault = tmp_path / "vault"
    engine = tmp_path / "engine"
    home.mkdir(parents=True)
    vault.mkdir(parents=True)
    engine.mkdir(parents=True)

    manifest_path = vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        """schema_version: 1
servers:
  some-server:
    tier: core
    transport: stdio
    command: echo
""",
        encoding="utf-8",
    )

    renderer = McpRenderer(vault_data=vault, engine_root=engine, home=home)
    cfg_file = openclaw_config(home)
    cfg_file.parent.mkdir(parents=True, exist_ok=True)

    # Configuration without CLI runtimes (e.g. direct OpenAI / Ollama API)
    initial_config = {
        "models": {
            "providers": {
                "openai": {
                    "apiKey": "sk-test",
                }
            }
        },
        "agents": {
            "entries": {
                "bot": {
                    "model": "openai/gpt-4o",
                }
            }
        },
        "mcp": {"servers": {}},
    }
    cfg_file.write_text(json.dumps(initial_config, indent=2), encoding="utf-8")

    ok, msg = renderer.render_openclaw(write=True)
    assert ok is True

    written = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert "some-server" in written["mcp"]["servers"]
    assert "tools" not in written or "byProvider" not in written.get("tools", {})
    assert "tools" not in written["agents"]["entries"]["bot"] or "deny" not in written["agents"]["entries"]["bot"]["tools"]


def _generate_odd_connector_names() -> list[str]:
    """Generate 50 arbitrary and odd connector names."""
    names = [
        "with-dash", "with.dot", "UPPER_CASE", "UPPER-WITH-DASH.AND.DOT",
        "openclaw_helper", "openclaw-extra", "openclaw.service", "OPENCLAW_UPPER",
        "server-123", "x",
    ]
    names.extend([f"connector-{i}" for i in range(1, 41)])
    return names


def test_property_loopback_guard_for_arbitrary_connectors() -> None:
    """Foundation 2: property-style test for arbitrary mounted connector names.

    For ANY set of server names mounted in mcp.servers (including odd ones: dash, dot,
    uppercase, starting with openclaw, 50 names at once), every mounted name except
    openclaw and lazy-mcp has its <name>__* pattern in the deny of every provider key
    routed to claude-cli, and lazy-mcp__* and openclaw__* appear in no deny.
    """
    odd_names = _generate_odd_connector_names()
    all_names = list(odd_names) + ["openclaw", "lazy-mcp"]

    doc: dict[str, Any] = {
        "models": {
            "providers": {
                "anthropic": {"agentRuntime": {"id": "claude-cli"}},
            }
        },
        "agents": {
            "entries": {
                "capo": {
                    "model": "anthropic/claude-3-5-sonnet",
                    "agentRuntime": {"id": "claude-cli"},
                },
            }
        },
        "mcp": {
            "servers": {name: {"command": "echo", "args": [name]} for name in all_names}
        },
    }

    conns = connector_names(doc)
    guarded = apply_loopback_guard(doc, conns)

    by_provider = guarded.get("tools", {}).get("byProvider", {})
    deny_list = by_provider["anthropic"]["deny"]
    for name in odd_names:
        expected = f"{name}__*"
        assert expected in deny_list, f"Missing pattern {expected} in provider anthropic"
    assert "openclaw__*" not in deny_list
    assert "lazy-mcp__*" not in deny_list

    agent_deny = guarded["agents"]["entries"]["capo"]["tools"]["deny"]
    for name in odd_names:
        expected = f"{name}__*"
        assert expected in agent_deny, f"Missing pattern {expected} in agent capo"
    assert "openclaw__*" not in agent_deny
    assert "lazy-mcp__*" not in agent_deny
