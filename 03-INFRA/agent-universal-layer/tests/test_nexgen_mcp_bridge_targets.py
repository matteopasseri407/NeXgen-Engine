"""What the bridge fronts: the connectors the renderer would have launched on this machine.

The point of these tests is that the list is not written anywhere a second time.
The bridge asks the renderer what the OpenClaw gateway mounts on this machine and
starts exactly that, so a connector added to the manifest appears at the bridge
without a line of bridge code, and one the plan routes elsewhere does not.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.mcp_bridge_targets import UnknownBridgeServer, select_backends, stdio_backends  # noqa: E402
from nexgen_core.renderer import McpRenderer  # noqa: E402

MANIFEST = """schema_version: 1
servers:
  local-tool:
    tier: core
    transport: stdio
    command: python3
    args: ["${AGENT_ENGINE_ROOT}/tools/local.py"]
    env: {DEMO_MODE: headless, EMPTY_ONE: ""}
  node-tool:
    tier: core
    transport: stdio
    command: node
    args: ["server.mjs"]
  remote:
    tier: core
    transport: http
    url: "https://example.invalid/mcp"
"""


class FakeRenderer:
    """A renderer's answer without a manifest: the bridge only ever asks this."""

    def __init__(self, servers: dict) -> None:
        self.servers = servers
        self.asked: list[str] = []

    def load_resolved_servers(self, cli_target: str) -> dict:
        self.asked.append(cli_target)
        return self.servers


def test_only_stdio_connectors_are_bridged():
    renderer = FakeRenderer({
        "stdio-one": {"command": "python", "args": ["one.py"], "env": {"A": "1", "EMPTY": ""}},
        "remote-one": {"url": "https://example.invalid/mcp"},
        "broken": {"command": "  "},
    })
    backends = stdio_backends(renderer)
    assert backends == {"stdio-one": {"command": ["python", "one.py"], "env": {"A": "1"}}}
    assert renderer.asked == ["openclaw"], "the placement rule is the OpenClaw gateway's"


def test_the_names_asked_for_are_the_names_served():
    renderer = FakeRenderer({
        "one": {"command": "python", "args": ["one.py"]},
        "two": {"command": "node", "args": ["two.mjs"]},
    })
    assert list(select_backends(["two"], renderer)) == ["two"]
    assert sorted(select_backends(None, renderer)) == ["one", "two"]


def test_an_unknown_name_lists_what_could_have_been_asked_for():
    renderer = FakeRenderer({"one": {"command": "python", "args": ["one.py"]}})
    with pytest.raises(UnknownBridgeServer) as raised:
        select_backends(["nope"], renderer)
    assert raised.value.names == ["nope"]
    assert raised.value.available == ["one"]
    assert "one" in str(raised.value)


def test_the_bridge_starts_what_the_renderer_would_have_written(tmp_path: Path, monkeypatch):
    """The resolved command is the renderer's, not a second expansion of the same manifest."""
    monkeypatch.delenv("DEMO_URL", raising=False)
    vault = tmp_path / "vault"
    manifest = vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(MANIFEST, encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    engine = tmp_path / "engine"
    renderer = McpRenderer(vault_data=vault, engine_root=engine, home=home)

    backends = stdio_backends(renderer)
    assert "remote" not in backends, "an HTTP connector already listens: it needs no bridge"
    assert backends["node-tool"] == {"command": ["node", "server.mjs"], "env": {}}
    python_backend = backends["local-tool"]
    assert python_backend["command"][0] in ("python3", "python"), "python3 is spelled python on Windows"
    assert str(engine) in python_backend["command"][1], "${AGENT_ENGINE_ROOT} is resolved to this machine"
    assert python_backend["env"] == {"DEMO_MODE": "headless"}
