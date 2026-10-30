"""A retired MCP server leaves every CLI, tombstone or not.

The engine remembers every server name it declared at a real render, so a
name removed from the manifest stops being rendered on the next sync even when
nobody added it to `retired_servers`. A name the engine never declared belongs
to whoever put it there and is preserved. Missing a tombstone costs a stale
connector that the CLI fails to start on every launch (#147: `lane`).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from nexgen_core import i18n, mcp_state
from nexgen_core.checks.mcp_checks import check_mcp_orphans
from nexgen_core.jsonc import parse_jsonc
from nexgen_core.renderer import McpRenderer
from nexgen_core.report import Severity


@pytest.fixture(autouse=True)
def _english():
    """The wording is the subject of the orphan tests: read it in the source language."""
    i18n.set_language("en")
    yield
    i18n.set_language(None)


def _server() -> dict[str, Any]:
    """A plain stdio connector that mounts directly on every CLI."""
    return {"transport": "stdio", "tier": "core", "command": "python3", "args": ["-V"]}


def _write_manifest(path: Path, servers: dict[str, Any], retired: tuple[str, ...] = ()) -> None:
    path.write_text(
        yaml.safe_dump(
            {"schema_version": 1, "retired_servers": list(retired), "servers": servers},
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _machine(tmp_path: Path, servers: dict[str, Any], retired: tuple[str, ...] = ()):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    vault = tmp_path / "vault"
    manifest = vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    _write_manifest(manifest, servers, retired)
    renderer = McpRenderer(vault_data=vault, engine_root=tmp_path / "engine", home=home)
    return renderer, home, vault, manifest


def _config_path(renderer: McpRenderer, cli: str) -> Path:
    if cli == "claude":
        return renderer.home / ".claude.json"
    return renderer.opencode_config_path()


def _seed(renderer: McpRenderer, cli: str, names: list[str]) -> None:
    """A config already on disk holding `names`, as a previous render or a person left it."""
    path = _config_path(renderer, cli)
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = {name: {"type": "local", "command": ["python3", "-V"]} for name in names}
    if cli == "claude":
        payload: dict[str, Any] = {"mcpServers": entries}
    else:
        payload = {"mcp": {"servers": entries}}
    path.write_text(json.dumps(payload), encoding="utf-8")


def _rendered(renderer: McpRenderer, cli: str) -> set[str]:
    path = _config_path(renderer, cli)
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw) if cli == "claude" else parse_jsonc(raw)
    if cli == "claude":
        return set(data.get("mcpServers", {}))
    return set(data.get("mcp", {}).get("servers", {}))


def _render(renderer: McpRenderer, cli: str, write: bool = True) -> None:
    ok, _ = getattr(renderer, f"render_{cli}")(write=write)
    assert ok


# ---- (a) an explicit tombstone still wins ------------------------------------

@pytest.mark.parametrize("cli", ["claude", "opencode"])
def test_a_tombstoned_server_disappears_from_the_rendered_config(
    tmp_path: Path, cli: str, opencode_dialect
) -> None:
    opencode_dialect("2.2.0")
    renderer, _home, _vault, _manifest = _machine(
        tmp_path, {"alpha": _server()}, retired=("lane",)
    )
    _seed(renderer, cli, ["alpha", "lane"])

    _render(renderer, cli)

    assert _rendered(renderer, cli) == {"alpha"}


# ---- (b) the engine remembers what it declared ------------------------------

@pytest.mark.parametrize("cli", ["claude", "opencode"])
def test_a_server_dropped_from_the_manifest_is_pruned_without_a_tombstone(
    tmp_path: Path, cli: str, opencode_dialect
) -> None:
    opencode_dialect("2.2.0")
    renderer, _home, _vault, manifest = _machine(
        tmp_path, {"alpha": _server(), "lane": _server()}
    )
    _render(renderer, cli)
    assert _rendered(renderer, cli) == {"alpha", "lane"}
    assert mcp_state.load_declared_servers(renderer.home) == {"alpha", "lane"}

    _write_manifest(manifest, {"alpha": _server()})
    _render(renderer, cli)

    assert _rendered(renderer, cli) == {"alpha"}


def test_a_cli_skipped_when_the_server_left_still_drops_it_later(tmp_path: Path, opencode_dialect) -> None:
    """The memory only grows: a config that was not rendered on the pass that
    removed `lane` must not keep it because the engine forgot the name."""
    opencode_dialect("2.2.0")
    renderer, _home, _vault, manifest = _machine(
        tmp_path, {"alpha": _server(), "lane": _server()}
    )
    _render(renderer, "claude")
    _render(renderer, "opencode")

    _write_manifest(manifest, {"alpha": _server()})
    _render(renderer, "claude")
    _render(renderer, "claude")
    _render(renderer, "opencode")

    assert _rendered(renderer, "opencode") == {"alpha"}


def test_one_sync_pass_removes_the_name_from_every_dialect(tmp_path: Path, opencode_dialect) -> None:
    """The memory is written after the whole pass: an early write would leave
    `lane` in each dialect rendered after the first one."""
    opencode_dialect("2.2.0")
    renderer, _home, _vault, manifest = _machine(
        tmp_path, {"alpha": _server(), "lane": _server()}
    )
    renderer.render_all(write=True)

    _write_manifest(manifest, {"alpha": _server()})
    results = renderer.render_all(write=True)

    assert results["claude"] is True
    assert results["opencode"] is True
    assert _rendered(renderer, "claude") == {"alpha"}
    assert _rendered(renderer, "opencode") == {"alpha"}


# ---- (c) what the engine never declared stays -------------------------------

@pytest.mark.parametrize("cli", ["claude", "opencode"])
def test_a_name_the_engine_never_declared_survives_every_render(
    tmp_path: Path, cli: str, opencode_dialect
) -> None:
    opencode_dialect("2.2.0")
    renderer, _home, _vault, _manifest = _machine(tmp_path, {"alpha": _server()})
    _seed(renderer, cli, ["alpha", "hand-added"])

    _render(renderer, cli)
    _render(renderer, cli)

    assert _rendered(renderer, cli) == {"alpha", "hand-added"}


def test_an_allowlisted_name_is_not_treated_as_engine_debris(
    tmp_path: Path, opencode_dialect
) -> None:
    """`orphans_allowlist` is a person saying "keep this": memory must not override it."""
    opencode_dialect("2.2.0")
    renderer, _home, _vault, manifest = _machine(
        tmp_path, {"alpha": _server(), "lane": _server()}
    )
    _render(renderer, "opencode")

    # `lane` leaves the manifest but the person kept it deliberately.
    _write_manifest(manifest, {"alpha": _server()})
    manifest.write_text(
        manifest.read_text(encoding="utf-8") + "\norphans_allowlist: [lane]\n",
        encoding="utf-8",
    )
    _render(renderer, "opencode")

    assert _rendered(renderer, "opencode") == {"alpha", "lane"}


# ---- (d) only a real render writes the memory -------------------------------

def test_a_dry_run_does_not_update_the_memory(tmp_path: Path, opencode_dialect) -> None:
    opencode_dialect("2.2.0")
    renderer, home, _vault, manifest = _machine(tmp_path, {"alpha": _server()})
    _render(renderer, "opencode")
    assert mcp_state.load_declared_servers(home) == {"alpha"}

    _write_manifest(manifest, {"beta": _server()})
    _render(renderer, "opencode", write=False)

    assert mcp_state.load_declared_servers(home) == {"alpha"}


def test_pending_changes_does_not_update_the_memory(tmp_path: Path, opencode_dialect) -> None:
    opencode_dialect("2.2.0")
    renderer, home, _vault, manifest = _machine(tmp_path, {"alpha": _server()})
    _render(renderer, "opencode")

    _write_manifest(manifest, {"beta": _server()})
    assert renderer.pending_changes("opencode")

    assert mcp_state.load_declared_servers(home) == {"alpha"}


def test_a_missing_or_corrupt_memory_is_simply_empty(tmp_path: Path, opencode_dialect) -> None:
    opencode_dialect("2.2.0")
    renderer, home, _vault, _manifest = _machine(tmp_path, {"alpha": _server()})
    path = mcp_state.mcp_state_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    _seed(renderer, "opencode", ["alpha", "hand-added"])

    _render(renderer, "opencode")

    assert _rendered(renderer, "opencode") == {"alpha", "hand-added"}
    assert mcp_state.load_declared_servers(home) == {"alpha"}  # and it recovers


# ---- (e) the orphan check tells debris from a person's server ---------------

def _claude_config(home: Path, names: list[str]) -> None:
    (home / ".claude.json").write_text(
        json.dumps({"mcpServers": {name: {"type": "stdio", "command": "x"} for name in names}}),
        encoding="utf-8",
    )


def test_a_retired_server_is_reported_as_debris_the_next_sync_removes(tmp_path: Path) -> None:
    _renderer, home, vault, _manifest = _machine(
        tmp_path, {"alpha": _server()}, retired=("lane",)
    )
    _claude_config(home, ["alpha", "lane"])

    outcome = check_mcp_orphans(vault, home)

    assert outcome.severity == Severity.WARN
    assert "lane" in outcome.message
    assert "retired" in outcome.message.lower()
    assert "nexgen sync" in outcome.action
    assert "adopt" not in outcome.action.lower()


def test_a_server_added_by_hand_still_gets_the_adoption_advice(tmp_path: Path) -> None:
    _renderer, home, vault, _manifest = _machine(tmp_path, {"alpha": _server()})
    _claude_config(home, ["alpha", "stray-server"])

    outcome = check_mcp_orphans(vault, home)

    assert outcome.severity == Severity.WARN
    assert "stray-server" in outcome.message
    assert "adopt" in outcome.action.lower()
    assert "retired" not in outcome.message.lower()


def test_a_config_with_both_kinds_names_each_group_for_what_it_is(tmp_path: Path) -> None:
    _renderer, home, vault, _manifest = _machine(
        tmp_path, {"alpha": _server()}, retired=("lane",)
    )
    _claude_config(home, ["alpha", "lane", "stray-server"])

    outcome = check_mcp_orphans(vault, home)

    assert outcome.severity == Severity.WARN
    assert "lane" in outcome.message
    assert "stray-server" in outcome.message
    assert "retired" in outcome.message.lower()
    assert "hand" in outcome.message.lower()
