"""Pinning dello split mcp_render/antigravity+claude_desktop prima del refactor.

Fissa il comportamento attuale (non quello desiderato) con server finti:
render su server finti ritorna config attesa, render stabile su doppia
chiamata, firme pubbliche invariate. Solo config finte in tmp_path, mai
config vere, mai rete.
"""
from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

import yaml

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.mcp_render import antigravity as antigravity_mod  # noqa: E402
from nexgen_core.mcp_render import claude_desktop as desktop_mod  # noqa: E402
from nexgen_core.mcp_render import MCP_REMOTE_PACKAGE  # noqa: E402
from nexgen_core.paths import antigravity_config, claude_desktop_config  # noqa: E402
from nexgen_core.renderer import McpRenderer  # noqa: E402


def _make_renderer(tmp_path: Path, servers: dict | None = None) -> McpRenderer:
    home = tmp_path / "home"
    vault = tmp_path / "vault"
    manifest = vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        yaml.safe_dump(
            {
                "servers": servers
                or {
                    "alpha": {
                        "transport": "stdio",
                        "command": "python",
                        "args": ["-V"],
                        "tier": "core",
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    home.mkdir(parents=True, exist_ok=True)
    return McpRenderer(vault_data=vault, engine_root=tmp_path / "engine", home=home)


def _desktop_cfg(renderer: McpRenderer) -> Path:
    return claude_desktop_config(renderer.home)


# ---------------------------------------------------------- Antigravity ---


def test_antigravity_render_su_server_finti_ritorna_config_attesa(tmp_path) -> None:
    renderer = _make_renderer(
        tmp_path,
        {
            "fake-stdio": {
                "transport": "stdio",
                "command": "fake-cmd",
                "args": ["--flag"],
                "tier": "core",
                "env": {"MODE": "headless"},
            },
            "fake-remote": {
                "transport": "http",
                "url": "https://example.invalid/mcp",
                "tier": "core",
            },
        },
    )
    ok, _msg = antigravity_mod.render(renderer, write=True)
    assert ok is True
    cfg = antigravity_config(renderer.home)
    assert str(cfg).startswith(str(tmp_path))
    assert cfg.is_file()
    data = json.loads(cfg.read_text(encoding="utf-8"))
    servers = data["mcpServers"]
    assert servers["fake-stdio"] == {
        "command": "fake-cmd",
        "args": ["--flag"],
        "env": {"MODE": "headless"},
    }
    remote = servers["fake-remote"]
    assert remote["command"].lower().startswith("node")
    assert remote["args"][1] == "https://example.invalid/mcp"
    assert remote["args"][3] == MCP_REMOTE_PACKAGE
    assert remote["env"] == {}


def test_antigravity_render_stabile_su_doppia_chiamata(tmp_path) -> None:
    renderer = _make_renderer(tmp_path)
    ok1, msg1 = antigravity_mod.render(renderer, write=True)
    assert ok1 is True
    cfg = antigravity_config(renderer.home)
    first = cfg.read_bytes()
    assert set(json.loads(first.decode("utf-8"))["mcpServers"]) == {"alpha"}
    ok2, msg2 = antigravity_mod.render(renderer, write=True)
    assert ok2 is True
    assert msg1 == msg2
    assert cfg.read_bytes() == first


# ------------------------------------------------------- Claude Desktop ---


def test_desktop_render_su_server_finti_ritorna_config_attesa(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    renderer = _make_renderer(
        tmp_path,
        {
            "alpha": {
                "transport": "stdio",
                "command": "/usr/bin/python3",
                "args": ["-V"],
                "tier": "core",
                "env": {"MODE": "x"},
                "desktop_allow": True,
            },
            "denied": {
                "transport": "stdio",
                "command": "/usr/bin/python3",
                "args": ["-V"],
                "tier": "core",
            },
        },
    )
    cfg = _desktop_cfg(renderer)
    assert str(cfg).startswith(str(tmp_path))
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({}), encoding="utf-8")
    ok, _msg = desktop_mod.render(renderer, write=True)
    assert ok is True
    table = json.loads(cfg.read_text(encoding="utf-8"))["mcpServers"]
    assert table["alpha"] == {"command": "/usr/bin/python3", "args": ["-V"], "env": {"MODE": "x"}}
    assert "denied" not in table
    assert set(desktop_mod.allowed_desktop_servers(renderer)) == {"alpha"}


def test_desktop_render_stabile_su_doppia_chiamata(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    renderer = _make_renderer(
        tmp_path,
        {
            "alpha": {
                "transport": "stdio",
                "command": "/usr/bin/python3",
                "args": ["-V"],
                "tier": "core",
                "desktop_allow": True,
            },
        },
    )
    cfg = _desktop_cfg(renderer)
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({}), encoding="utf-8")
    ok1, msg1 = desktop_mod.render(renderer, write=True)
    assert ok1 is True
    first = cfg.read_bytes()
    assert "alpha" in json.loads(first.decode("utf-8"))["mcpServers"]
    ok2, msg2 = desktop_mod.render(renderer, write=True)
    assert ok2 is True
    assert msg1 == msg2
    assert cfg.read_bytes() == first


# ---------------------------------------------------------------- firme ---


def test_firme_pubbliche_invariate() -> None:
    assert list(inspect.signature(antigravity_mod.render).parameters) == ["renderer", "write"]
    assert list(inspect.signature(desktop_mod.render).parameters) == ["renderer", "write"]
    public_ag = {
        name
        for name, value in vars(antigravity_mod).items()
        if callable(value)
        and not name.startswith("_")
        and getattr(value, "__module__", None) == antigravity_mod.__name__
    }
    assert public_ag == {"render"}
    public_dt = {
        name
        for name, value in vars(desktop_mod).items()
        if callable(value)
        and not name.startswith("_")
        and getattr(value, "__module__", None) == desktop_mod.__name__
    }
    assert public_dt == {"allowed_desktop_servers", "gated_desktop_names", "render"}
