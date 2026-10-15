"""Pinning dello split renderer prima del refactor.

Fissa il comportamento attuale (non quello desiderato) con manifest
finti: resolve su entry finta ritorna server attesi, load su manifest
finto stabile, firme pubbliche invariate. Solo manifest e config finti
in tmp_path, mai config vere, mai rete.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import yaml

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.renderer import McpRenderer  # noqa: E402


def make_renderer(tmp_path, servers=None):
    home = tmp_path / "home"
    vault = tmp_path / "vault"
    manifest = vault / "03-INFRA/agent-universal-layer/mcp/manifest.yaml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(yaml.safe_dump({"servers": servers or {
        "alpha": {"transport": "stdio", "command": "python", "args": ["-V"], "tier": "core"},
    }}))
    home.mkdir(parents=True, exist_ok=True)
    return McpRenderer(vault_data=vault, engine_root=tmp_path / "engine", home=home)


def test_resolve_su_entry_finta_ritorna_server_attesi(tmp_path, monkeypatch) -> None:
    renderer = make_renderer(tmp_path)
    entry = renderer._resolve_entry(
        "alpha",
        {"command": "python", "args": ["-V"], "tier": "core", "deps": ["x"]},
        "claude",
    )
    assert entry is not None
    assert entry["command"] == "python"
    assert entry["args"] == ["-V"]
    assert "deps" not in entry

    monkeypatch.delenv("NEXGEN_TEST_RENDERER_GATE", raising=False)
    gated = {"command": "python", "tier": "core", "require_env": "NEXGEN_TEST_RENDERER_GATE"}
    assert renderer._resolve_entry("gated", dict(gated), "claude") is None
    monkeypatch.setenv("NEXGEN_TEST_RENDERER_GATE", "1")
    assert renderer._resolve_entry("gated", dict(gated), "claude") is not None

    assert renderer._resolve_entry("empty", {"command": "", "tier": "core"}, "claude") is None
    url_entry = renderer._resolve_entry(
        "web", {"url": "https://example.invalid/mcp", "tier": "core"}, "claude"
    )
    assert url_entry is not None
    assert url_entry["url"] == "https://example.invalid/mcp"


def test_load_su_manifest_finto_stabile(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("NEXGEN_TEST_RENDERER_GATE", raising=False)
    renderer = make_renderer(tmp_path, {
        "alpha": {"transport": "stdio", "command": "python", "args": ["-V"], "tier": "core"},
        "beta": {"transport": "stdio", "command": "python", "args": ["-V"], "tier": "core"},
        "gated": {"transport": "stdio", "command": "python", "tier": "core",
                  "require_env": "NEXGEN_TEST_RENDERER_GATE"},
    })
    first = renderer.load_resolved_servers("claude")
    second = renderer.load_resolved_servers("claude")
    assert set(first) == {"alpha", "beta"}
    assert first == second
    assert set(renderer.manifest_server_names()) == {"alpha", "beta", "gated"}


def test_firme_pubbliche_invariate() -> None:
    assert list(inspect.signature(McpRenderer.load_resolved_servers).parameters) == ["self", "cli_target"]
    assert list(inspect.signature(McpRenderer.manifest_server_names).parameters) == ["self"]
    assert list(inspect.signature(McpRenderer.retired_server_names).parameters) == ["self"]
    assert list(inspect.signature(McpRenderer._resolve_entry).parameters) == [
        "self", "name", "srv", "cli_target", "gateway",
    ]
    assert list(inspect.signature(McpRenderer.render_all).parameters) == ["self", "write"]
