"""Pinning dello split mcp_render/opencode prima del refactor.

Fissa il comportamento attuale (non quello desiderato) con server finti:
render su server finti ritorna config attesa, render stabile su doppia
chiamata, firme pubbliche invariate. Solo config finte in tmp_path, mai
config vere, mai rete.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import yaml

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.mcp_render import opencode as opencode_mod  # noqa: E402
from nexgen_core.jsonc import parse_jsonc  # noqa: E402
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


def _isolate(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("NEXGEN_HOME", raising=False)


def test_render_su_server_finti_ritorna_config_attesa(tmp_path, monkeypatch) -> None:
    _isolate(tmp_path, monkeypatch)
    renderer = _make_renderer(
        tmp_path,
        {
            "fake-stdio": {
                "transport": "stdio",
                "command": "fake-cmd",
                "args": ["--flag"],
                "tier": "core",
                "env": {"MODE": "headless"},
                "timeouts": {"startup": 30, "tool": 120},
            },
            "fake-remote": {
                "transport": "http",
                "url": "https://example.invalid/mcp",
                "tier": "core",
                "timeouts": {"startup": 15, "tool": 60},
            },
        },
    )
    ok, _msg = opencode_mod.render(renderer, write=True)
    assert ok is True
    cfg = renderer.opencode_config_path()
    assert cfg.is_file()
    assert str(cfg).startswith(str(tmp_path))
    data = parse_jsonc(cfg.read_text(encoding="utf-8"))
    servers = data["mcp"]["servers"]
    assert servers["fake-stdio"] == {
        "type": "local",
        "command": ["fake-cmd", "--flag"],
        "environment": {"MODE": "headless"},
        "timeout": {"startup": 30000, "execution": 120000},
    }
    assert servers["fake-remote"] == {
        "type": "remote",
        "url": "https://example.invalid/mcp",
        "timeout": {"startup": 15000, "execution": 60000},
    }


def test_render_stabile_su_doppia_chiamata(tmp_path, monkeypatch) -> None:
    _isolate(tmp_path, monkeypatch)
    renderer = _make_renderer(tmp_path)
    ok1, msg1 = opencode_mod.render(renderer, write=True)
    assert ok1 is True
    cfg = renderer.opencode_config_path()
    assert str(cfg).startswith(str(tmp_path))
    first = cfg.read_bytes()
    data = parse_jsonc(first.decode("utf-8"))
    assert set(data["mcp"]["servers"]) == {"alpha"}
    ok2, msg2 = opencode_mod.render(renderer, write=True)
    assert ok2 is True
    assert msg1 == msg2
    assert cfg.read_bytes() == first


def test_firme_pubbliche_invariate() -> None:
    assert list(inspect.signature(opencode_mod.render).parameters) == ["renderer", "write"]
    public = {
        name
        for name, value in vars(opencode_mod).items()
        if callable(value)
        and not name.startswith("_")
        and getattr(value, "__module__", None) == opencode_mod.__name__
    }
    assert public == {"render"}
