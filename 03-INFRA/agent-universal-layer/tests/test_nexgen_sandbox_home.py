"""NEXGEN_HOME moves the connectors' tokens and the Council's sessions too."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

INFRA = Path(__file__).resolve().parents[2]
for extra in (INFRA / "scripts", INFRA / "agent-universal-layer" / "council"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from nexgen_local.connectors import auth, outlook, token_store  # noqa: E402


def test_connector_tokens_follow_nexgen_home(tmp_path, monkeypatch):
    sandbox = tmp_path / "sandbox"
    monkeypatch.setenv("NEXGEN_HOME", str(sandbox))
    monkeypatch.delenv("WORKSPACE_MCP_TOKEN_DIR", raising=False)
    monkeypatch.delenv("OUTLOOK_TOKEN_DIR", raising=False)
    assert auth.token_dir() == sandbox / ".config" / "nexgen-workspace-mcp"
    assert outlook.token_dir() == sandbox / ".config" / "nexgen-outlook"


def test_an_explicit_token_dir_still_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXGEN_HOME", str(tmp_path / "sandbox"))
    monkeypatch.setenv("WORKSPACE_MCP_TOKEN_DIR", str(tmp_path / "elsewhere"))
    assert token_store.resolve_token_dir("WORKSPACE_MCP_TOKEN_DIR", "x") == tmp_path / "elsewhere"


def test_without_a_sandbox_the_real_home_is_used(monkeypatch):
    monkeypatch.delenv("NEXGEN_HOME", raising=False)
    monkeypatch.delenv("WORKSPACE_MCP_TOKEN_DIR", raising=False)
    assert auth.token_dir() == Path.home() / ".config" / "nexgen-workspace-mcp"


@pytest.mark.skipif(os.name == "nt", reason="the POSIX state layout")
def test_council_sessions_follow_nexgen_home(tmp_path, monkeypatch):
    import session

    sandbox = tmp_path / "sandbox"
    monkeypatch.setenv("NEXGEN_HOME", str(sandbox))
    assert session._local_state_root() == sandbox / ".local" / "state"
    monkeypatch.delenv("NEXGEN_HOME")
    assert session._local_state_root() == Path.home() / ".local" / "state"
