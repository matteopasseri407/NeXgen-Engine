"""Testing ClawHub plugin MCP wrapper adoption and non-wrapper classification.

Every test runs against temporary folders with fake plugin installations,
fake configurations, and synthetic SQLite snapshots. Nothing touches any
real user directory or launches any external process.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import i18n  # noqa: E402
from nexgen_core.config import load_mcp_manifest  # noqa: E402
from nexgen_core.openclaw.adopt import run_adopt  # noqa: E402
from nexgen_core.openclaw.adopt_plugins import read_catalog_snapshots, scan_plugins  # noqa: E402
from nexgen_core.openclaw.adopt_scan import scan  # noqa: E402


@pytest.fixture(autouse=True)
def _english():
    i18n.set_language("en")
    yield
    i18n.set_language(None)


def _create_fake_sqlite_catalog(state_dir: Path, entries: list[dict]) -> Path:
    """Seed openclaw.sqlite with a catalog snapshot row."""
    db_dir = state_dir / "state"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "openclaw.sqlite"
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS official_external_plugin_catalog_snapshots (
            feed_url TEXT NOT NULL PRIMARY KEY,
            body TEXT NOT NULL,
            status INTEGER NOT NULL,
            etag TEXT,
            last_modified TEXT,
            checksum TEXT NOT NULL,
            saved_at TEXT NOT NULL,
            trust_mode TEXT,
            trust_key_id TEXT,
            trust_signature_count INTEGER,
            trust_threshold INTEGER,
            trust_verified_at TEXT,
            updated_at_ms INTEGER NOT NULL
        )
    """)
    body = json.dumps({"schemaVersion": 1, "entries": entries})
    cursor.execute(
        "INSERT INTO official_external_plugin_catalog_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("https://catalog.invalid/feed", body, 200, None, None, "chk", "2026-10-24", None, None, None, None, None, 0),
    )
    conn.commit()
    conn.close()
    return db_path


def test_plugin_with_mcpservers_in_manifest_is_extracted(tmp_path):
    state = tmp_path / "state"
    plugin_dir = state / "extensions" / "github-plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "openclaw.plugin.json").write_text(json.dumps({
        "id": "github-plugin",
        "name": "GitHub",
        "mcpServers": {
            "github": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github"],
            },
        },
    }), encoding="utf-8")

    servers, notes = scan_plugins(state, config={"plugins": {"entries": {"github-plugin": {}}}}, declared=set())

    assert len(servers) == 1
    assert servers[0].name == "github"
    assert servers[0].spec["command"] == "npx"
    assert servers[0].spec["args"] == ["-y", "@modelcontextprotocol/server-github"]
    assert servers[0].spec["transport"] == "stdio"
    assert notes == []


def test_plugin_with_http_mcpserver_and_env_ref_keeps_reference(tmp_path):
    state = tmp_path / "state"
    plugin_dir = state / "extensions" / "hosted-tool"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "openclaw.plugin.json").write_text(json.dumps({
        "id": "hosted-tool",
        "mcpServers": {
            "hosted-tool": {
                "url": "https://api.example.com/mcp",
                "auth": {"type": "bearer", "env": "TOOL_API_KEY"},
            },
        },
    }), encoding="utf-8")

    servers, notes = scan_plugins(state, config={"plugins": {"entries": {"hosted-tool": {}}}}, declared=set())

    assert len(servers) == 1
    assert servers[0].name == "hosted-tool"
    assert servers[0].spec["transport"] == "http"
    assert servers[0].spec["url"] == "https://api.example.com/mcp"
    assert servers[0].spec["auth"] == {"type": "bearer", "env": "TOOL_API_KEY"}
    assert notes == []


def test_bundle_plugin_with_mcp_json_is_extracted(tmp_path):
    state = tmp_path / "state"
    plugin_dir = state / "extensions" / "agent-bundle"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "mcp.json").write_text(json.dumps({
        "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
        "mcpServers": {
            "bundled-server": {
                "type": "stdio",
                "command": "node",
                "args": ["./server.js"],
            },
        },
    }), encoding="utf-8")

    servers, _notes = scan_plugins(state, config={"plugins": {"entries": {"agent-bundle": {}}}}, declared=set())

    assert len(servers) == 1
    assert servers[0].name == "bundled-server"
    assert servers[0].spec["command"] == "node"
    assert servers[0].spec["transport"] == "stdio"


def test_clawhub_plugin_with_catalog_wrapper_description_and_config(tmp_path):
    state = tmp_path / "state"
    _create_fake_sqlite_catalog(state, [{
        "name": "@openclaw/linear",
        "description": "Linear project tools via official MCP service",
        "kind": "plugin",
        "openclaw": {
            "plugin": {"id": "linear", "label": "Linear"},
        },
    }])

    config = {
        "plugins": {
            "entries": {
                "linear": {
                    "enabled": True,
                    "config": {
                        "url": "https://mcp.linear.app",
                        "apiKey": "${LINEAR_API_KEY}",
                    },
                },
            },
        },
    }

    servers, notes = scan_plugins(state, config=config, declared=set())

    assert read_catalog_snapshots(state)["linear"]["description"] == "Linear project tools via official MCP service"
    assert len(servers) == 1
    assert servers[0].name == "linear"
    assert servers[0].spec["transport"] == "http"
    assert servers[0].spec["url"] == "https://mcp.linear.app"
    assert servers[0].spec["auth"] == {"type": "bearer", "env": "LINEAR_API_KEY"}
    assert notes == []


def test_non_wrapper_plugins_produce_one_line_notes_and_no_servers(tmp_path):
    state = tmp_path / "state"
    # Channel plugin
    (state / "extensions" / "slack").mkdir(parents=True)
    (state / "extensions" / "slack" / "openclaw.plugin.json").write_text(json.dumps({
        "id": "slack",
        "kind": "channel",
        "description": "Slack channel integration",
    }), encoding="utf-8")

    # Memory plugin
    (state / "extensions" / "lancedb").mkdir(parents=True)
    (state / "extensions" / "lancedb" / "openclaw.plugin.json").write_text(json.dumps({
        "id": "lancedb",
        "kind": "memory",
        "description": "LanceDB vector memory",
    }), encoding="utf-8")

    # Provider plugin
    (state / "extensions" / "ollama").mkdir(parents=True)
    (state / "extensions" / "ollama" / "openclaw.plugin.json").write_text(json.dumps({
        "id": "ollama",
        "kind": "provider",
        "description": "Ollama local inference provider",
    }), encoding="utf-8")

    config = {
        "plugins": {
            "entries": {
                "slack": {"enabled": True},
                "lancedb": {"enabled": True},
                "ollama": {"enabled": True},
            },
        },
    }

    servers, notes = scan_plugins(state, config=config, declared=set())

    assert servers == []
    assert len(notes) == 3
    assert any("slack" in n and "channel" in n and "not adoptable" in n for n in notes)
    assert any("lancedb" in n and "memory" in n and "not adoptable" in n for n in notes)
    assert any("ollama" in n and "provider" in n and "not adoptable" in n for n in notes)


def test_already_declared_server_is_omitted(tmp_path):
    state = tmp_path / "state"
    plugin_dir = state / "extensions" / "github-plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "openclaw.plugin.json").write_text(json.dumps({
        "id": "github-plugin",
        "mcpServers": {
            "github": {"command": "npx"},
        },
    }), encoding="utf-8")

    servers, _notes = scan_plugins(state, config={"plugins": {"entries": {"github-plugin": {}}}}, declared={"github"})

    assert servers == []


def test_adopt_scan_and_plan_integration(tmp_path):
    state = tmp_path / "state"
    # An MCP wrapper plugin
    (state / "extensions" / "github-plugin").mkdir(parents=True)
    (state / "extensions" / "github-plugin" / "openclaw.plugin.json").write_text(json.dumps({
        "id": "github-plugin",
        "mcpServers": {
            "github": {"command": "npx", "args": ["-y", "github-mcp"]},
        },
    }), encoding="utf-8")
    # A non-wrapper channel plugin
    (state / "extensions" / "discord").mkdir(parents=True)
    (state / "extensions" / "discord" / "openclaw.plugin.json").write_text(json.dumps({
        "id": "discord",
        "kind": "channel",
    }), encoding="utf-8")

    config_path = state / "openclaw.json"
    config_path.write_text(json.dumps({
        "plugins": {
            "entries": {
                "github-plugin": {"enabled": True},
                "discord": {"enabled": True},
            },
        },
    }), encoding="utf-8")

    found = scan(
        config_path=config_path,
        state_dir=state,
        home=tmp_path / "home",
        skills_manifest={"skills": {}},
        mcp_manifest={"servers": {}},
    )

    assert [s.name for s in found.servers] == ["github"]
    assert any("discord" in note and "channel" in note for note in found.notes)


def test_run_adopt_adopts_wrapper_server_and_reports_non_wrapper_note(tmp_path):
    home = tmp_path / "home"
    state = home / ".openclaw"
    state.mkdir(parents=True)
    (state / "extensions" / "linear").mkdir(parents=True)
    (state / "extensions" / "linear" / "openclaw.plugin.json").write_text(json.dumps({
        "id": "linear",
        "description": "Linear issues via official MCP service",
    }), encoding="utf-8")
    (state / "extensions" / "telegram").mkdir(parents=True)
    (state / "extensions" / "telegram" / "openclaw.plugin.json").write_text(json.dumps({
        "id": "telegram",
        "kind": "channel",
    }), encoding="utf-8")

    config_path = state / "openclaw.json"
    config_path.write_text(json.dumps({
        "plugins": {
            "entries": {
                "linear": {
                    "enabled": True,
                    "config": {"url": "https://mcp.linear.app/mcp", "apiKey": "${LINEAR_TOKEN}"},
                },
                "telegram": {"enabled": True},
            },
        },
    }), encoding="utf-8")

    vault = tmp_path / "vault"
    mcp_folder = vault / "03-INFRA" / "agent-universal-layer" / "mcp"
    mcp_folder.mkdir(parents=True)
    manifest = mcp_folder / "manifest.yaml"
    manifest.write_text("schema_version: 1\nservers:\n  lazy-mcp:\n    exposure: eager\n    tier: core\n    transport: stdio\n    command: python3\n    args: ['lazy-mcp.py']\n    targets: [claude, codex, antigravity, opencode, openclaw]\n", encoding="utf-8")

    skills_folder = vault / "03-INFRA" / "agent-universal-layer" / "skills"
    skills_folder.mkdir(parents=True)
    (skills_folder / "skills.manifest.yaml").write_text("schema_version: 1\nskills: {}\n", encoding="utf-8")

    printed = []
    code = run_adopt(
        home=home,
        vault_data=vault,
        state_dir=state,
        config_path=config_path,
        yes=True,
        out=printed.append,
    )

    assert code == 0
    output_text = "\n".join(printed)
    assert "mcp linear: safe to adopt" in output_text
    assert "note: plugin 'telegram' is not an MCP wrapper (channel): not adoptable" in output_text

    data = load_mcp_manifest(manifest)
    assert "linear" in data["servers"]
    assert data["servers"]["linear"]["url"] == "https://mcp.linear.app/mcp"
    assert data["servers"]["linear"]["auth"] == {"env": "LINEAR_TOKEN"}
