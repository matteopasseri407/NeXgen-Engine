"""Pinning dello split mcp_check prima del refactor.

Fissa il comportamento attuale (non quello desiderato) con gateway e
server finti: check_gateway su gateway finto ritorna esito, verify_servers
su server finti stabile, firme pubbliche invariate. Solo tmp_path, solo
finti, nessuna rete, nessun gateway vero.
"""
from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

import pytest
import yaml

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import mcp_check  # noqa: E402
from nexgen_core.mcp_placement import GATEWAY, GATEWAY_CLI_ENV  # noqa: E402

CLI = "claude"
PIN_SRV = "pin-srv"
PIN_DIRECT = "pin-direct"


class _FakeRenderer:
    """Renderer finto: manifest vero in tmp_path, voci risolte finte in tmp_path."""

    def __init__(self, manifest_path: Path, resolved: dict) -> None:
        self.manifest_path = manifest_path
        self._resolved = resolved

    def load_resolved_servers(self, cli: str) -> dict:
        return dict(self._resolved.get(cli, {}))


class _FakeSession:
    """Sessione MCP finta: mai processi veri, mai rete, solo risposte canned."""

    tmp_root: str = ""
    served: dict = {}
    spawned: list = []

    def __init__(self, argv: list[str], env: dict[str, str], cwd=None) -> None:
        assert self.tmp_root and any(self.tmp_root in str(part) for part in argv), (
            f"solo finti in tmp_path, ricevuto: {argv!r}"
        )
        type(self).spawned.append(list(argv))
        self._env = dict(env or {})

    def handshake(self, timeout: float) -> None:
        return None

    def notify(self, method: str) -> None:
        return None

    def request(self, method: str, params: dict | None, timeout: float) -> dict:
        if method == "tools/list":
            return {"tools": [{"name": n} for n in sorted(mcp_check.META_TOOLS)]}
        if method == "tools/call":
            assert params and params.get("name") == "lazy_list"
            cli = self._env.get(GATEWAY_CLI_ENV, CLI)
            return {"content": [{"text": json.dumps({"cli": cli, "servers": dict(self.served)})}]}
        raise AssertionError(f"metodo inatteso: {method}")

    def close(self) -> None:
        return None


def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.request

    def _blocked(*args, **kwargs):
        raise AssertionError("rete toccata")

    monkeypatch.setattr(urllib.request, "urlopen", _blocked)


def _make_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Manifest finto in tmp_path piu tre script finti mai eseguiti."""
    _no_network(monkeypatch)
    monkeypatch.setenv("AGENT_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)

    fake_gw = tmp_path / "fake-gateway.py"
    fake_srv = tmp_path / "fake-srv.py"
    fake_direct = tmp_path / "fake-direct.py"
    for p in (fake_gw, fake_srv, fake_direct):
        p.write_text("# finto, mai eseguito\n", encoding="utf-8")

    manifest_path = tmp_path / "manifest.yaml"
    manifest_path.write_text(
        yaml.safe_dump(
            {
                "servers": {
                    PIN_SRV: {
                        "exposure": "lazy",
                        "command": sys.executable,
                        "args": [str(fake_srv)],
                        "targets": [CLI],
                    },
                    PIN_DIRECT: {
                        "exposure": "eager",
                        "command": sys.executable,
                        "args": [str(fake_direct)],
                        "targets": [CLI],
                    },
                    GATEWAY: {
                        "exposure": "eager",
                        "command": sys.executable,
                        "args": [str(fake_gw)],
                        "targets": [CLI],
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    resolved = {
        CLI: {
            GATEWAY: {
                "command": sys.executable,
                "args": [str(fake_gw)],
                "env": {GATEWAY_CLI_ENV: CLI},
            },
            PIN_DIRECT: {
                "command": sys.executable,
                "args": [str(fake_direct)],
                "env": {},
            },
        }
    }
    renderer = _FakeRenderer(manifest_path, resolved)

    _FakeSession.tmp_root = str(tmp_path)
    _FakeSession.served = {PIN_SRV: {}}
    _FakeSession.spawned = []
    monkeypatch.setattr(mcp_check, "_Session", _FakeSession)
    return renderer


def test_fake_gateway_returns_outcome(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    renderer = _make_layout(tmp_path, monkeypatch)
    outcome = mcp_check.check_gateway(renderer, CLI, timeout=10)
    assert outcome.cli == CLI
    assert outcome.ok is True
    assert outcome.served == [PIN_SRV]
    assert outcome.problems == []
    assert outcome.unavailable == set()
    assert outcome.seconds >= 0
    assert _FakeSession.spawned, "il gateway finto deve essere stato contattato"
    assert all(str(tmp_path) in str(part) for argv in _FakeSession.spawned for part in argv if str(tmp_path) in str(part))


def test_fake_gateway_stable_when_run_twice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    renderer = _make_layout(tmp_path, monkeypatch)
    first = mcp_check.check_gateway(renderer, CLI, timeout=10)
    second = mcp_check.check_gateway(renderer, CLI, timeout=10)
    assert (second.served, second.problems, second.ok) == (first.served, first.problems, first.ok)


def test_verify_fake_servers_stable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    renderer = _make_layout(tmp_path, monkeypatch)
    first = mcp_check.verify_servers(renderer, [PIN_SRV, PIN_DIRECT], timeout=10)
    second = mcp_check.verify_servers(renderer, [PIN_SRV, PIN_DIRECT], timeout=10)
    assert first == []
    assert second == first


def test_verify_ignores_unknown_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    renderer = _make_layout(tmp_path, monkeypatch)
    assert mcp_check.verify_servers(renderer, ["nothing-by-that-name"], timeout=5) == []


def test_public_signatures_unchanged() -> None:
    assert list(inspect.signature(mcp_check.check_gateway).parameters) == ["renderer", "cli", "timeout"]
    assert list(inspect.signature(mcp_check.check_direct).parameters) == ["renderer", "cli", "name", "timeout"]
    assert list(inspect.signature(mcp_check.verify_servers).parameters) == ["renderer", "names", "timeout"]
    assert list(inspect.signature(mcp_check.main).parameters) == ["clis", "timeout"]
    assert list(inspect.signature(mcp_check.main_direct).parameters) == ["clis", "timeout"]
