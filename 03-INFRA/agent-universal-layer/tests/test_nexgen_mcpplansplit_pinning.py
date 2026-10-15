"""Pinning dello split mcp_plan prima del refactor.

Fissa il comportamento attuale (non quello desiderato) con plan finti:
build su server finti ritorna piano atteso, render stampa piano stabile,
firme pubbliche invariate. Solo finti in tmp_path, mai config vere,
mai rete.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest
import yaml

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import mcp_plan  # noqa: E402

PIN_DIRECT = "pin-direct"
PIN_LAZY = "pin-lazy"
GATEWAY = "lazy-mcp"


def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket
    import urllib.request

    def _blocked(*args, **kwargs):
        raise AssertionError("rete toccata")

    monkeypatch.setattr(urllib.request, "urlopen", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


def _fake_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Manifest finto in tmp_path; state finto in tmp_path."""
    _no_network(monkeypatch)
    assert str(tmp_path) and Path(tmp_path).is_dir()
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("AGENT_STATE_DIR", str(state))
    manifest = tmp_path / "manifest.yaml"
    assert str(manifest).startswith(str(tmp_path)), "solo finti in tmp_path"
    manifest.write_text(
        yaml.safe_dump(
            {
                "servers": {
                    GATEWAY: {"exposure": "eager", "command": "fake-cmd"},
                    PIN_DIRECT: {
                        "exposure": "eager",
                        "command": "fake-cmd",
                        "targets": ["claude"],
                    },
                    PIN_LAZY: {
                        "exposure": "lazy",
                        "command": "fake-cmd",
                        "targets": ["claude"],
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    return manifest


def test_build_su_server_finti_ritorna_piano_atteso(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _fake_manifest(tmp_path, monkeypatch)
    plan = mcp_plan.build(manifest)
    assert {r["server"] for r in plan["rows"]} == {GATEWAY, PIN_DIRECT, PIN_LAZY}
    by_name = {r["server"]: r for r in plan["rows"]}
    assert by_name[PIN_DIRECT]["cells"]["claude"] == "direct"
    assert by_name[PIN_DIRECT]["cells"]["codex"] == "absent"
    assert by_name[PIN_LAZY]["cells"]["claude"] == "gateway"
    assert by_name[PIN_LAZY]["cells"]["codex"] == "absent"
    assert by_name[PIN_LAZY]["why"] == ["exposure: lazy"]
    assert plan["gateway"]["claude"] == [PIN_LAZY]
    assert plan["gateway"]["codex"] == []
    assert plan["problems"] == []
    assert mcp_plan.build(manifest) == plan


def test_render_stampa_piano_stabile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _fake_manifest(tmp_path, monkeypatch)
    plan = mcp_plan.build(manifest)
    first = mcp_plan.render(plan)
    second = mcp_plan.render(plan)
    assert first == second
    for name in (GATEWAY, PIN_DIRECT, PIN_LAZY):
        assert name in first
    assert "gateway" in first
    assert f"claude: {PIN_LAZY}" in first


def test_firme_pubbliche_invariate() -> None:
    assert list(inspect.signature(mcp_plan.build).parameters) == ["manifest_path"]
    assert list(inspect.signature(mcp_plan.render).parameters) == ["plan"]
    assert list(inspect.signature(mcp_plan.main).parameters) == ["as_json"]
