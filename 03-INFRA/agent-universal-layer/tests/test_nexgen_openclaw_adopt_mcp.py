"""Adopting a live MCP server: one manifest row, lazy, with no credential in it.

The write path is the engine's own (`mcp_add.insert_entry`), so an adopted
server is indistinguishable from one added with `nexgen mcp add`: validated,
atomic, backed up, and refused when the name is taken.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import i18n  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.config import load_mcp_manifest  # noqa: E402
from nexgen_core.mcp_placement import CLIS  # noqa: E402
from nexgen_core.openclaw.adopt_mcp import adopt_server, manifest_entry  # noqa: E402
from nexgen_core.openclaw.adopt_scan import UnmanagedServer  # noqa: E402

A_MANIFEST = {
    "schema_version": 1,
    "servers": {
        "lazy-mcp": {
            "exposure": "eager",
            "tier": "core",
            "transport": "stdio",
            "command": "python3",
            "args": ["${AGENT_ENGINE_ROOT}/agent-universal-layer/mcp/lazy-mcp.py"],
            "targets": list(CLIS),
        },
    },
}


@pytest.fixture(autouse=True)
def _english():
    i18n.set_language("en")
    yield
    i18n.set_language(None)


@pytest.fixture
def a_vault(tmp_path) -> Path:
    vault = tmp_path / "vault"
    folder = vault / "03-INFRA" / "agent-universal-layer" / "mcp"
    folder.mkdir(parents=True)
    (folder / "manifest.yaml").write_text(yaml.safe_dump(A_MANIFEST, sort_keys=False), encoding="utf-8")
    return vault


def a_server(name: str = "github", **spec) -> UnmanagedServer:
    return UnmanagedServer(name=name, spec=spec)


def a_manifest(vault: Path) -> dict:
    return yaml.safe_load((vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml").read_text("utf-8"))


# ------------------------------------------------------------- the manifest row


def test_a_live_stdio_server_becomes_a_lazy_row_for_every_cli():
    entry = manifest_entry(a_server(command="npx", args=["-y", "some-mcp"], env={"LOG": "debug"}))

    assert entry["transport"] == "stdio"
    assert entry["command"] == "npx"
    assert entry["args"] == ["-y", "some-mcp"]
    assert entry["env"] == {"LOG": "debug"}
    assert entry["exposure"] == "lazy"
    assert entry["lazy"] is True
    assert entry["tier"] == "core"
    assert entry["targets"] == list(CLIS)
    assert entry["origin"] == "openclaw"
    assert entry["openclaw_id"] == "github"


def test_a_live_http_server_keeps_a_reference_and_never_the_token():
    entry = manifest_entry(a_server(
        url="https://example.test/mcp",
        transport="streamable-http",
        headers={"Authorization": "Bearer ${GITHUB_TOKEN}"},
    ))

    assert entry["transport"] == "http"
    assert entry["url"] == "https://example.test/mcp"
    assert entry["auth"] == {"env": "GITHUB_TOKEN"}


def test_a_token_written_out_in_full_is_refused_rather_than_placeholder_ed():
    with pytest.raises(ValueError) as caught:
        manifest_entry(a_server(url="https://example.test/mcp", headers={"Authorization": "Bearer ghp_realtoken"}))

    assert "${VARIABLE}" in str(caught.value)


def test_a_spec_with_neither_a_url_nor_a_command_is_refused():
    with pytest.raises(ValueError):
        manifest_entry(a_server())


def test_a_remote_server_with_environment_variables_is_refused_not_silently_trimmed():
    with pytest.raises(ValueError) as caught:
        manifest_entry(a_server(url="https://example.test/mcp", env={"REGION": "eu"}))

    assert "nothing was dropped" in str(caught.value)


# ---------------------------------------------------------------- the write path


def test_adopting_a_server_declares_it_lazily_and_touches_nothing_else(a_vault):
    outcome = adopt_server(a_server(command="npx", args=["-y", "some-mcp"]), vault_data=a_vault)

    assert outcome.adopted, outcome.note
    servers = load_mcp_manifest(a_vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml")["servers"]
    assert sorted(servers) == ["github", "lazy-mcp"]
    assert servers["github"]["exposure"] == "lazy"
    assert servers["github"]["origin"] == "openclaw"
    assert servers["github"]["openclaw_id"] == "github"
    assert servers["github"]["targets"] == list(CLIS)
    assert servers["lazy-mcp"] == A_MANIFEST["servers"]["lazy-mcp"]
    assert (a_vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml").read_text("utf-8").count("servers:") == 1


def test_a_taken_name_is_refused_and_the_file_is_unchanged(a_vault):
    before = a_manifest(a_vault)

    outcome = adopt_server(a_server(name="lazy-mcp", command="npx"), vault_data=a_vault)

    assert not outcome.adopted
    assert "already declared" in outcome.note
    assert a_manifest(a_vault) == before


def test_an_entry_the_engine_cannot_write_is_refused_with_its_reason(a_vault):
    outcome = adopt_server(a_server(name="odd", command="npx\nrm -rf /"), vault_data=a_vault)

    assert not outcome.adopted
    assert a_manifest(a_vault) == A_MANIFEST


def test_a_manifest_without_the_gateway_gets_it_because_nothing_lazy_is_reachable_without_it(tmp_path):
    vault = tmp_path / "vault"
    folder = vault / "03-INFRA" / "agent-universal-layer" / "mcp"
    folder.mkdir(parents=True)
    (folder / "manifest.yaml").write_text("schema_version: 1\nservers: {}\n", encoding="utf-8")

    outcome = adopt_server(a_server(command="npx"), vault_data=vault)

    assert outcome.adopted, outcome.note
    assert sorted(load_mcp_manifest(folder / "manifest.yaml")["servers"]) == ["github", "lazy-mcp"]
