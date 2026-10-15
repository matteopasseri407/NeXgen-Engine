"""Pinning dello split mcp_add prima del refactor.

Fissa il comportamento attuale (non quello desiderato) con manifest finti
in tmp_path: build_entry su server finto ritorna la voce attesa,
insert_entry scrive solo se cambia (dry-run e duplicato non scrivono),
firme pubbliche invariate. Mai config vere, mai rete.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.mcp_add import add_server, build_entry, insert_entry, parse_targets  # noqa: E402

_FAKE_MANIFEST = """\
schema_version: 1
servers:
  fake-existing:
    tier: core
    exposure: eager
    transport: stdio
    command: fake-cmd
    args: ["--flag"]
    targets: [claude]
"""


def _fake_vault(tmp_path: Path) -> tuple[Path, Path]:
    vault = tmp_path / "vault"
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    manifest = vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(_FAKE_MANIFEST, encoding="utf-8")
    return vault, home


def _manifest_path(vault: Path) -> Path:
    return vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"


def test_build_entry_fake_stdio_returns_expected() -> None:
    entry = build_entry(
        name="pin-demo",
        targets=["codex"],
        command="npx",
        args=["-y", "pin-demo-mcp@1.0.0"],
        url=None,
        auth_env=None,
        env=None,
        lazy=False,
        readonly=False,
    )
    assert entry == {
        "tier": "core",
        "exposure": "eager",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "pin-demo-mcp@1.0.0"],
        "targets": ["codex"],
    }


def test_insert_entry_writes_only_on_change(tmp_path: Path) -> None:
    vault, home = _fake_vault(tmp_path)
    manifest = _manifest_path(vault)
    entry = build_entry(
        name="pin-demo",
        targets=["codex"],
        command="npx",
        args=["-y", "pin-demo-mcp@1.0.0"],
        url=None,
        auth_env=None,
        env=None,
        lazy=False,
        readonly=False,
    )
    before = manifest.read_text(encoding="utf-8")

    code, message = insert_entry("pin-demo", entry, home=home, vault_data=vault)
    assert code == 0, message
    after = manifest.read_text(encoding="utf-8")
    assert after != before
    assert "pin-demo" in after

    code2, message2 = insert_entry("pin-demo", entry, home=home, vault_data=vault)
    assert code2 == 2
    assert "already declared" in message2
    assert manifest.read_text(encoding="utf-8") == after

    code3, _ = insert_entry("pin-other", entry, dry_run=True, home=home, vault_data=vault)
    assert code3 == 0
    assert manifest.read_text(encoding="utf-8") == after
    assert "pin-other" not in manifest.read_text(encoding="utf-8")


def test_firme_pubbliche_invariate() -> None:
    assert list(inspect.signature(parse_targets).parameters) == ["raw"]
    assert list(inspect.signature(build_entry).parameters) == [
        "name", "targets", "command", "args", "url", "auth_env", "env", "lazy", "readonly",
    ]
    assert list(inspect.signature(insert_entry).parameters) == [
        "name", "entry", "dry_run", "home", "vault_data", "targets",
    ]
    assert list(inspect.signature(add_server).parameters) == [
        "name", "targets_raw", "command", "args", "url", "auth_env",
        "env_pairs", "lazy", "readonly", "dry_run", "home", "vault_data",
    ]
