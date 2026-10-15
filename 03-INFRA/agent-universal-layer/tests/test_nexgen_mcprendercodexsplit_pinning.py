"""Pinning dello split mcp_render/codex prima del refactor.

Fissa il comportamento attuale (non quello desiderato) con server finti:
render su server finti ritorna config attesa, render stabile su doppia
chiamata, firme pubbliche invariate. Solo config finte in tmp_path, mai
config vere, mai rete.
"""
from __future__ import annotations

import inspect
import sys
import tomllib
from pathlib import Path

import yaml

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.mcp_render import codex as codex_mod  # noqa: E402
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


def _codex_file(renderer: McpRenderer) -> Path:
    from nexgen_core.paths import codex_config

    return codex_config(renderer.home)


def test_render_su_server_finti_ritorna_config_attesa(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("CODEX_HOME", raising=False)
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
    ok, _msg = codex_mod.render(renderer, write=True)
    assert ok is True
    cfg = _codex_file(renderer)
    assert cfg.is_file()
    parsed = tomllib.loads(cfg.read_text(encoding="utf-8"))["mcp_servers"]
    assert parsed["fake_stdio"]["command"] == "fake-cmd"
    assert parsed["fake_stdio"]["args"] == ["--flag"]
    assert parsed["fake_stdio"]["env"] == {"MODE": "headless"}
    assert parsed["fake_stdio"]["startup_timeout_sec"] == 30.0
    assert parsed["fake_stdio"]["tool_timeout_sec"] == 120.0
    assert parsed["fake_remote"]["url"] == "https://example.invalid/mcp"
    assert parsed["fake_remote"]["startup_timeout_sec"] == 15.0
    assert parsed["fake_remote"]["tool_timeout_sec"] == 60.0


def test_render_stabile_su_doppia_chiamata(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("CODEX_HOME", raising=False)
    renderer = _make_renderer(tmp_path)
    ok1, msg1 = codex_mod.render(renderer, write=True)
    assert ok1 is True
    cfg = _codex_file(renderer)
    first = cfg.read_bytes()
    assert tomllib.loads(first.decode("utf-8"))["mcp_servers"].keys() == {"alpha"}
    ok2, msg2 = codex_mod.render(renderer, write=True)
    assert ok2 is True
    assert msg1 == msg2
    assert cfg.read_bytes() == first


def test_firme_pubbliche_invariate() -> None:
    assert list(inspect.signature(codex_mod.render).parameters) == ["renderer", "write"]
    public = {
        name
        for name, value in vars(codex_mod).items()
        if callable(value)
        and not name.startswith("_")
        and getattr(value, "__module__", None) == codex_mod.__name__
    }
    assert public == {"render"}
