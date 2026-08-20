"""Controlli di configurazione e disponibilità server MCP."""
from __future__ import annotations

import json

from pathlib import Path

from nexgen_core.config import load_mcp_manifest
from nexgen_core.renderer import McpRenderer
from nexgen_core.report import CheckOutcome, Severity


def check_mcp_manifest(manifest_path: Path) -> CheckOutcome:
    """Verifica che mcp/manifest.yaml esista e sia valido."""
    if not manifest_path.is_file():
        return CheckOutcome(
            id="mcp.manifest_present",
            severity=Severity.BROKEN,
            message=f"Il manifest MCP '{manifest_path}' non esiste.",
            action="Crea o ripristina mcp/manifest.yaml dai template.",
        )

    try:
        data = load_mcp_manifest(manifest_path)
        server_count = len(data.get("servers", {}))
        return CheckOutcome(
            id="mcp.manifest_valid",
            severity=Severity.OK,
            message=f"Manifest MCP valido con {server_count} server dichiarati",
        )
    except Exception as exc:
        return CheckOutcome(
            id="mcp.manifest_valid",
            severity=Severity.BROKEN,
            message=f"Il manifest MCP contiene errori: {exc}",
            action="Correggi la sintassi di mcp/manifest.yaml.",
        )


def check_mcp_configs_rendered(vault_data: Path, home: Path) -> CheckOutcome:
    """Verifica che i file di configurazione MCP delle CLI siano generati e contengano i server attesi."""
    renderer = McpRenderer(vault_data=vault_data, home=home)
    manifest_path = vault_data / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    
    def remedy() -> bool:
        renderer.render_all(write=True)
        return True

    expected_claude = renderer.load_resolved_servers("claude")
    expected_agy = renderer.load_resolved_servers("antigravity")
    expected_opencode = renderer.load_resolved_servers("opencode")

    claude_cfg = home / ".claude.json"
    agy_cfg = home / ".gemini" / "antigravity-ide" / "mcp_config.json"
    opencode_cfg = renderer._opencode_config_path()

    # Se nessun file esiste
    if not claude_cfg.exists() and not agy_cfg.exists() and not opencode_cfg.exists():
        return CheckOutcome(
            id="mcp.rendered_configs",
            severity=Severity.BROKEN,
            message="I file di configurazione MCP per le CLI non sono ancora stati generati.",
            action="Esegui 'agent-sync apply' per generarli.",
            remedy=remedy,
        )

    # Verifica che almeno una CLI configurata contenga i server attesi
    has_servers = False
    if claude_cfg.is_file():
        try:
            data = json.loads(claude_cfg.read_text(encoding="utf-8"))
            if any(srv in data.get("mcpServers", {}) for srv in expected_claude):
                has_servers = True
        except Exception:
            pass

    if agy_cfg.is_file():
        try:
            data = json.loads(agy_cfg.read_text(encoding="utf-8"))
            if any(srv in data.get("mcpServers", {}) for srv in expected_agy):
                has_servers = True
        except Exception:
            pass

    if opencode_cfg.is_file():
        try:
            from nexgen_core.jsonc import parse_jsonc
            data = parse_jsonc(opencode_cfg.read_text(encoding="utf-8")) if opencode_cfg.suffix == ".jsonc" else json.loads(opencode_cfg.read_text(encoding="utf-8"))
            if any(srv in data.get("mcp", {}) for srv in expected_opencode):
                has_servers = True
        except Exception:
            pass

    if not has_servers and (expected_claude or expected_agy or expected_opencode):
        return CheckOutcome(
            id="mcp.rendered_configs",
            severity=Severity.BROKEN,
            message="I file di configurazione MCP esistono ma non contengono i server dichiarati nel manifest.",
            action="Esegui 'agent-sync apply' per rigenerarli.",
            remedy=remedy,
        )

    return CheckOutcome(
        id="mcp.rendered_configs",
        severity=Severity.OK,
        message="File di configurazione MCP generati e allineati",
    )
