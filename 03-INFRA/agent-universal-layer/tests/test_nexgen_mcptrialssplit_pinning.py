"""Pinning dello split mcp_trials prima del refactor.

Fissa il comportamento attuale (non quello desiderato) con trial finti
in tmp_path: start su trial finto ritorna esito, cmd_try finto stabile,
firme pubbliche invariate. Solo tmp_path, solo finti, nessuna rete,
nessun trial vero, nessuna config vera.
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

from nexgen_core import mcp_trials  # noqa: E402

PIN_NAME = "pin-fake"
PIN_NOW = 1_700_000_000.0


def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket
    import urllib.request

    def _blocked(*args, **kwargs):
        raise AssertionError("rete toccata")

    monkeypatch.setattr(urllib.request, "urlopen", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


def _fake_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Manifest finto in tmp_path con gateway; state finto in tmp_path."""
    _no_network(monkeypatch)
    vault = tmp_path / "vault"
    manifest = vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        yaml.safe_dump(
            {
                "servers": {
                    "lazy-mcp": {"tier": "core", "command": "x"},
                    "real": {"exposure": "lazy", "command": "x"},
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_VAULT_DATA", str(vault))
    monkeypatch.setenv("AGENT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("NEXGEN_HOME", str(tmp_path / "home"))
    return manifest


def test_start_fake_trial_returns_expiry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_env(tmp_path, monkeypatch)
    entry = {"command": "fake-cmd", "exposure": "lazy"}
    expires = mcp_trials.start(PIN_NAME, entry, 2.0, manifest_names=set(), now=PIN_NOW)
    assert expires == pytest.approx(PIN_NOW + 2.0 * 3600)
    running = mcp_trials.active(now=PIN_NOW + 10)
    assert PIN_NAME in running
    assert running[PIN_NAME]["entry"] == entry
    assert running[PIN_NAME]["expires"] == expires
    assert PIN_NAME in mcp_trials.overlay({}, now=PIN_NOW + 10)
    assert mcp_trials.active(now=expires + 1) == {}


def test_cmd_try_fake_stable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = _fake_env(tmp_path, monkeypatch)
    before = manifest.read_text(encoding="utf-8")
    first = mcp_trials.cmd_try(
        PIN_NAME,
        targets_raw="all",
        command="npx",
        args=["-y", "pin-fake-mcp@1.0.0"],
        url=None,
        auth_env=None,
        env_pairs=None,
        readonly=False,
        hours=2.0,
    )
    second = mcp_trials.cmd_try(
        PIN_NAME,
        targets_raw="all",
        command="npx",
        args=["-y", "pin-fake-mcp@1.0.0"],
        url=None,
        auth_env=None,
        env_pairs=None,
        readonly=False,
        hours=2.0,
    )
    assert first[0] == 0
    assert second[0] == 0
    assert PIN_NAME in first[1] and PIN_NAME in second[1]
    assert PIN_NAME in mcp_trials.active()
    assert manifest.read_text(encoding="utf-8") == before, "un trial non tocca mai il manifest"


def test_start_refuses_manifest_name_and_bad_hours(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_env(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        mcp_trials.start("real", {"command": "x"}, 1.0, manifest_names={"real"}, now=PIN_NOW)
    with pytest.raises(ValueError):
        mcp_trials.start(PIN_NAME, {"command": "x"}, 0, manifest_names=set(), now=PIN_NOW)
    with pytest.raises(ValueError):
        mcp_trials.start(PIN_NAME, {"command": "x"}, mcp_trials.MAX_HOURS + 1, manifest_names=set(), now=PIN_NOW)
    assert mcp_trials.active(now=PIN_NOW) == {}


def test_firme_pubbliche_invariate() -> None:
    assert list(inspect.signature(mcp_trials.start).parameters) == [
        "name",
        "entry",
        "hours",
        "manifest_names",
        "now",
    ]
    assert list(inspect.signature(mcp_trials.cmd_try).parameters) == [
        "name",
        "targets_raw",
        "command",
        "args",
        "url",
        "auth_env",
        "env_pairs",
        "readonly",
        "hours",
    ]
