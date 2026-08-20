"""Controlli di configurazione e disponibilità server MCP."""
from __future__ import annotations

import json
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from nexgen_core.config import load_mcp_manifest
from nexgen_core.jsonc import parse_jsonc
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


def _rendered_names_claude_style(path: Path) -> set[str]:
    """Nomi server per Claude e Antigravity: entrambi scrivono `mcpServers`
    in un JSON puro."""
    data = json.loads(path.read_text(encoding="utf-8"))
    servers = data.get("mcpServers", {})
    return set(servers.keys()) if isinstance(servers, dict) else set()


def _rendered_names_opencode(path: Path) -> set[str]:
    raw = path.read_text(encoding="utf-8")
    data = parse_jsonc(raw) if path.suffix == ".jsonc" else json.loads(raw)
    servers = data.get("mcp", {}) if isinstance(data, dict) else {}
    return set(servers.keys()) if isinstance(servers, dict) else set()


def _rendered_names_codex(path: Path) -> set[str]:
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    servers = data.get("mcp_servers", {})
    return set(servers.keys()) if isinstance(servers, dict) else set()


def _identity(name: str) -> str:
    return name


def _codex_section_name(name: str) -> str:
    """Codex normalizza i trattini in underscore nei nomi di sezione TOML
    (vedi McpRenderer.render_codex)."""
    return name.replace("-", "_")


@dataclass(frozen=True)
class _CliRenderSpec:
    reader: Callable[[Path], set[str]]
    normalize: Callable[[str], str]


#: Per ogni CLI: come leggere i nomi dei server già resi dalla sua config
#: nativa e come normalizzare un nome atteso prima del confronto (solo
#: Codex rimappa i trattini).
_CLI_RENDER_SPECS: dict[str, _CliRenderSpec] = {
    "claude": _CliRenderSpec(_rendered_names_claude_style, _identity),
    "antigravity": _CliRenderSpec(_rendered_names_claude_style, _identity),
    "codex": _CliRenderSpec(_rendered_names_codex, _codex_section_name),
    "opencode": _CliRenderSpec(_rendered_names_opencode, _identity),
}


def check_mcp_configs_rendered(vault_data: Path, home: Path) -> CheckOutcome:
    """Confronta, per ciascuna CLI, l'insieme di server attesi dal manifest
    con quello effettivamente reso nella sua config nativa.

    Un server atteso ma mancante nella config resa è un drift che l'utente
    non ha scelto: BROKEN. Un server presente nella config ma fuori dal
    manifest non è per forza un problema (il render preserva additivamente
    i server live dell'utente): viene solo annotato nel dettaglio, non fa
    fallire il controllo. Una CLI mai lanciata (nessun file di config) non è
    un guasto: è undetermined, perché quella CLI potrebbe semplicemente non
    essere mai stata usata su questa macchina.
    """
    renderer = McpRenderer(vault_data=vault_data, home=home)

    def remedy() -> bool:
        renderer.render_all(write=True)
        return True

    cli_paths = {
        "claude": home / ".claude.json",
        "antigravity": home / ".gemini" / "antigravity-ide" / "mcp_config.json",
        "codex": home / ".codex" / "config.toml",
        "opencode": renderer._opencode_config_path(),
    }

    broken_parts: list[str] = []
    undetermined_parts: list[str] = []
    extra_parts: list[str] = []

    for cli, path in cli_paths.items():
        expected = set(renderer.load_resolved_servers(cli).keys())
        if not expected:
            continue  # niente atteso per questa CLI su questo manifest

        if not path.is_file():
            undetermined_parts.append(f"{cli} (mai lanciata: manca {path})")
            continue

        spec = _CLI_RENDER_SPECS[cli]
        try:
            rendered = spec.reader(path)
        except Exception as exc:
            undetermined_parts.append(f"{cli} (config illeggibile: {exc})")
            continue

        norm_expected = {spec.normalize(name): name for name in expected}
        missing_norm = set(norm_expected) - rendered
        missing = {norm_expected[n] for n in missing_norm}
        extra = rendered - set(norm_expected)

        if missing:
            broken_parts.append(f"{cli}: mancano {', '.join(sorted(missing))}")
        if extra:
            extra_parts.append(f"{cli}: {', '.join(sorted(extra))}")

    if broken_parts:
        return CheckOutcome(
            id="mcp.rendered_configs",
            severity=Severity.BROKEN,
            message="Le config MCP di alcune CLI non hanno tutti i server attesi dal manifest: " + "; ".join(broken_parts),
            action="Esegui 'agent-sync apply' per rigenerarle.",
            remedy=remedy,
            detail=("Server extra (non nel manifest, preservati additivamente): " + "; ".join(extra_parts)) if extra_parts else None,
        )
    if undetermined_parts:
        return CheckOutcome(
            id="mcp.rendered_configs",
            severity=Severity.UNDETERMINED,
            message="Alcune CLI non risultano ancora avviate su questa macchina, impossibile verificarne la config MCP: " + "; ".join(undetermined_parts),
        )

    return CheckOutcome(
        id="mcp.rendered_configs",
        severity=Severity.OK,
        message="File di configurazione MCP generati e allineati per tutte le CLI attive",
    )
