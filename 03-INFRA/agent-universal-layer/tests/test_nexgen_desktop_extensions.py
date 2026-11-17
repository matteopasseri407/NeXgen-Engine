"""The doctor sees the MCP servers Claude Desktop runs as extensions, not only its config table."""
from __future__ import annotations

import json
from pathlib import Path

from nexgen_core.checks import desktop_extensions
from nexgen_core.report import Severity


def _app(tmp_path: Path, switches: dict[str, object]) -> Path:
    """An app folder with one installed extension per switch; `None` means no switch file."""
    app = tmp_path / "Claude"
    (app / desktop_extensions.SWITCHES).mkdir(parents=True)
    inventory = {
        ext_id: {"id": ext_id, "manifest": {"name": ext_id.split(".")[-1], "display_name": ext_id.split(".")[-1].title()}}
        for ext_id in switches
    }
    (app / desktop_extensions.INVENTORY).write_text(json.dumps({"extensions": inventory}), encoding="utf-8")
    for ext_id, state in switches.items():
        if state is not None:
            (app / desktop_extensions.SWITCHES / f"{ext_id}.json").write_text(json.dumps({"isEnabled": state}), encoding="utf-8")
    (app / "claude_desktop_config.json").write_text("{}", encoding="utf-8")
    return app


def _vault(tmp_path: Path, allowlist: list[str]) -> Path:
    manifest = tmp_path / "vault" / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("servers: {}\norphans_allowlist: " + json.dumps(allowlist) + "\n", encoding="utf-8")
    return tmp_path / "vault"


def _run(monkeypatch, tmp_path: Path, switches: dict[str, object], allowlist: list[str]):
    app = _app(tmp_path, switches)
    monkeypatch.setattr(desktop_extensions, "claude_desktop_config", lambda home=None: app / "claude_desktop_config.json")
    return desktop_extensions.check_desktop_extensions(_vault(tmp_path, allowlist), tmp_path)


def test_only_enabled_extensions_are_named(monkeypatch, tmp_path):
    outcome = _run(monkeypatch, tmp_path, {"a.spotify": True, "a.blender": False, "a.fusion": None}, [])

    assert outcome.severity == Severity.OK  # the app's own installs: named, never a warning
    assert "a.spotify" in outcome.message
    assert "a.blender" not in outcome.message and "a.fusion" not in outcome.message


def test_an_acknowledged_extension_is_ok(monkeypatch, tmp_path):
    outcome = _run(monkeypatch, tmp_path, {"a.blender": True}, ["claude-desktop:a.blender"])

    assert outcome.severity == Severity.OK and "a.blender" not in outcome.message


def test_no_app_folder_is_ok(monkeypatch, tmp_path):
    monkeypatch.setattr(desktop_extensions, "claude_desktop_config", lambda home=None: tmp_path / "none" / "c.json")

    assert desktop_extensions.check_desktop_extensions(tmp_path, tmp_path).severity == Severity.OK
