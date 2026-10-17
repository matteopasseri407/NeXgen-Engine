"""Pinning dei check MCP principali (mcp_checks) su vault finto in tmp_path.

Fissa il comportamento attuale, non quello desiderato: se il refactor
(raggruppamento per dominio + blocco SHARED) cambia un esito, questi test
devono fallire. Nessuna rete, nessuna HOME reale, solo tmp_path.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.checks import mcp_checks  # noqa: E402
from nexgen_core.report import Severity  # noqa: E402

_MANIFEST = """\
schema_version: 1
servers:
  demo-server:
    tier: core
    transport: stdio
    command: echo
    args: ["hi"]
    targets: [claude]
"""

_GIT_REV = "b" * 40


def _vault_with_manifest(tmp_path: Path, extra: str = "") -> tuple[Path, Path]:
    vault = tmp_path / "vault"
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    manifest = vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(_MANIFEST + extra, encoding="utf-8")
    return vault, home


def _manifest_path(vault: Path) -> Path:
    return vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"


def _write_claude(home: Path, *names: str) -> Path:
    cfg = home / ".claude.json"
    cfg.write_text(json.dumps({"mcpServers": {n: {} for n in names}}), encoding="utf-8")
    return cfg


def test_valid_manifest_is_promoted(tmp_path: Path):
    vault, _home = _vault_with_manifest(tmp_path)
    outcome = mcp_checks.check_mcp_manifest(_manifest_path(vault))
    assert outcome.id == "mcp.manifest_valid"
    assert outcome.severity == Severity.OK
    assert "demo-server" not in outcome.message  # il messaggio conta, non elenca
    assert "1" in outcome.message  # un solo server dichiarato


def test_missing_manifest_is_broken(tmp_path: Path):
    vault = tmp_path / "vault"
    outcome = mcp_checks.check_mcp_manifest(_manifest_path(vault))
    assert outcome.severity == Severity.BROKEN
    assert outcome.id == "mcp.manifest_present"


def test_orphan_is_reported_with_path(tmp_path: Path):
    vault, home = _vault_with_manifest(tmp_path)
    _write_claude(home, "demo-server", "stray-server")
    outcome = mcp_checks.check_mcp_orphans(vault, home)
    assert outcome.id == "mcp.orphans"
    assert outcome.severity == Severity.WARN
    assert "stray-server" in outcome.message
    assert ".claude.json" in outcome.message


def test_allowlist_is_respected(tmp_path: Path):
    vault, home = _vault_with_manifest(
        tmp_path, extra='\norphans_allowlist: ["stray-server"]\n'
    )
    _write_claude(home, "demo-server", "stray-server")
    outcome = mcp_checks.check_mcp_orphans(vault, home)
    assert outcome.id == "mcp.orphans"
    assert outcome.severity == Severity.OK


def test_cli_scoped_allowlist_is_respected(tmp_path: Path):
    vault, home = _vault_with_manifest(
        tmp_path, extra='\norphans_allowlist: ["claude:stray-server"]\n'
    )
    _write_claude(home, "demo-server", "stray-server")
    outcome = mcp_checks.check_mcp_orphans(vault, home)
    assert outcome.severity == Severity.OK


def test_deps_ok_when_nothing_declared(tmp_path: Path):
    vault, _home = _vault_with_manifest(tmp_path)
    outcome = mcp_checks.check_mcp_deps(_manifest_path(vault), tmp_path / "state")
    assert outcome.id == "mcp.deps"
    assert outcome.severity == Severity.OK


def test_deps_warn_when_workspace_missing(tmp_path: Path):
    vault, _home = _vault_with_manifest(
        tmp_path,
        extra=(
            "  need-git:\n"
            "    tier: core\n"
            "    transport: stdio\n"
            "    command: echo\n"
            '    args: ["hi"]\n'
            "    targets: [claude]\n"
            f"    deps: {{kind: git, repo: \"https://example.invalid/r.git\", rev: \"{_GIT_REV}\"}}\n"
        ).replace("  need-git:", "\n  need-git:"),
    )
    outcome = mcp_checks.check_mcp_deps(_manifest_path(vault), tmp_path / "state")
    assert outcome.id == "mcp.deps"
    assert outcome.severity == Severity.WARN
    assert "need-git" in outcome.message


def test_outcome_is_stable_when_run_twice(tmp_path: Path):
    vault, home = _vault_with_manifest(tmp_path)
    _write_claude(home, "demo-server", "stray-server")

    first_manifest = mcp_checks.check_mcp_manifest(_manifest_path(vault))
    second_manifest = mcp_checks.check_mcp_manifest(_manifest_path(vault))
    assert (second_manifest.severity, second_manifest.id, second_manifest.message) == (
        first_manifest.severity, first_manifest.id, first_manifest.message,
    )

    first_orphans = mcp_checks.check_mcp_orphans(vault, home)
    second_orphans = mcp_checks.check_mcp_orphans(vault, home)
    assert (second_orphans.severity, second_orphans.id, second_orphans.message) == (
        first_orphans.severity, first_orphans.id, first_orphans.message,
    )
