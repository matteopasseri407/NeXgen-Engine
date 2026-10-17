"""Pinning dello split renderer_cli prima del refactor.

Fissa il comportamento attuale (non quello desiderato) con manifest
e config finte in tmp_path: cmd_adopt su config finta adotta,
insert_server_stubs stabile, firme pubbliche invariate. Solo config
finte in tmp_path, mai config vere, mai rete.
"""
from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import renderer_cli  # noqa: E402
from nexgen_core.config import load_mcp_manifest  # noqa: E402


_FAKE_MANIFEST = """\
schema_version: 1
servers:
  fake-known:
    tier: core
    transport: stdio
    command: fake-cmd
    targets: [claude]
"""


def _write_manifest(path: Path, text: str = _FAKE_MANIFEST) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _write_live_claude(path: Path, servers: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
    return path


def _isolate_renderer_cli(monkeypatch, manifest: Path, live: Path, lock: Path) -> None:
    monkeypatch.setattr(renderer_cli, "_manifest_path", lambda: manifest)
    monkeypatch.setattr(renderer_cli, "_cli_config_path", lambda cli: live)
    monkeypatch.setattr(renderer_cli, "_manifest_lock_path", lambda: lock)
    lock.parent.mkdir(parents=True, exist_ok=True)


def test_cmd_adopt_su_config_finta_adotta(tmp_path, monkeypatch, capsys) -> None:
    manifest = _write_manifest(tmp_path / "vault" / "manifest.yaml")
    live = _write_live_claude(tmp_path / "home" / ".claude.json", {
        "fake-known": {"command": "fake-cmd"},
        "pin-extra": {"command": "fake-cmd", "args": ["--flag"]},
    })
    lock = tmp_path / "state" / "mcp-manifest.lock"
    _isolate_renderer_cli(monkeypatch, manifest, live, lock)
    before = manifest.read_text(encoding="utf-8")

    rc = renderer_cli.cmd_adopt("claude", apply=False)
    assert rc == 0
    out = capsys.readouterr().out
    assert "pin-extra" in out
    assert manifest.read_text(encoding="utf-8") == before

    rc = renderer_cli.cmd_adopt("claude", apply=True)
    assert rc == 0
    out = capsys.readouterr().out
    assert "pin-extra" in out
    data = load_mcp_manifest(manifest)
    assert "fake-known" in data["servers"]
    assert "pin-extra" in data["servers"]
    assert data["servers"]["pin-extra"]["command"] == "fake-cmd"

    capsys.readouterr()
    rc = renderer_cli.cmd_adopt("claude", apply=False)
    assert rc == 0
    out = capsys.readouterr().out
    assert "nothing to adopt" in out or "niente da adottare" in out


def test_insert_server_stubs_stabile(tmp_path, monkeypatch) -> None:
    manifest = _write_manifest(tmp_path / "vault" / "manifest.yaml")
    lock = tmp_path / "state" / "mcp-manifest.lock"
    monkeypatch.setattr(renderer_cli, "_manifest_lock_path", lambda: lock)
    lock.parent.mkdir(parents=True, exist_ok=True)

    stub = renderer_cli._emit_manifest_stub(
        "pin-stub", {"transport": "stdio", "command": "fake-cmd", "targets": ["claude"]}
    )
    ok, message, bak = renderer_cli.insert_server_stubs(manifest, [stub])
    assert ok, message
    assert bak is not None
    assert bak.exists()
    first = manifest.read_text(encoding="utf-8")
    assert "pin-stub" in first

    data_first = load_mcp_manifest(manifest)
    data_second = load_mcp_manifest(manifest)
    assert data_first["servers"] == data_second["servers"]
    assert "pin-stub" in data_second["servers"]
    assert "fake-known" in data_second["servers"]
    assert manifest.read_text(encoding="utf-8") == first


def test_firme_pubbliche_invariate() -> None:
    assert list(inspect.signature(renderer_cli.cmd_write).parameters) == ["cli"]
    assert list(inspect.signature(renderer_cli.cmd_revert).parameters) == ["cli"]
    assert list(inspect.signature(renderer_cli.cmd_reset).parameters) == ["cli"]
    assert list(inspect.signature(renderer_cli.insert_server_stubs).parameters) == [
        "manifest_path",
        "stubs",
    ]
    assert list(inspect.signature(renderer_cli.cmd_adopt).parameters) == ["cli", "apply"]
    assert list(inspect.signature(renderer_cli.cmd_inventory).parameters) == []
    assert list(inspect.signature(renderer_cli.main).parameters) == ["argv"]
