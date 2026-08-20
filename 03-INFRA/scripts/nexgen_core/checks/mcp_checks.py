"""Checks for MCP server configuration and availability."""
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
    """Check that mcp/manifest.yaml exists and is valid."""
    if not manifest_path.is_file():
        return CheckOutcome(
            id="mcp.manifest_present",
            severity=Severity.BROKEN,
            message=f"The MCP manifest '{manifest_path}' does not exist.",
            action="Create or restore mcp/manifest.yaml from the templates.",
        )

    try:
        data = load_mcp_manifest(manifest_path)
        server_count = len(data.get("servers", {}))
        return CheckOutcome(
            id="mcp.manifest_valid",
            severity=Severity.OK,
            message=f"MCP manifest valid with {server_count} servers declared",
        )
    except Exception as exc:
        return CheckOutcome(
            id="mcp.manifest_valid",
            severity=Severity.BROKEN,
            message=f"The MCP manifest contains errors: {exc}",
            action="Fix the syntax of mcp/manifest.yaml.",
        )


def _rendered_names_claude_style(path: Path) -> set[str]:
    """Server names for Claude and Antigravity: both write `mcpServers`
    in plain JSON."""
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
    """Codex normalizes hyphens to underscores in TOML section names
    (see McpRenderer.render_codex)."""
    return name.replace("-", "_")


@dataclass(frozen=True)
class _CliRenderSpec:
    reader: Callable[[Path], set[str]]
    normalize: Callable[[str], str]


#: For each CLI: how to read the server names already rendered in its
#: native config, and how to normalize an expected name before comparing
#: (only Codex remaps hyphens).
_CLI_RENDER_SPECS: dict[str, _CliRenderSpec] = {
    "claude": _CliRenderSpec(_rendered_names_claude_style, _identity),
    "antigravity": _CliRenderSpec(_rendered_names_claude_style, _identity),
    "codex": _CliRenderSpec(_rendered_names_codex, _codex_section_name),
    "opencode": _CliRenderSpec(_rendered_names_opencode, _identity),
}


def check_mcp_configs_rendered(vault_data: Path, home: Path) -> CheckOutcome:
    """Compares, for each CLI, the set of servers expected by the manifest
    with the set actually rendered in its native config.

    A server expected but missing from the rendered config is drift the
    user didn't choose: BROKEN. A server present in the config but outside
    the manifest isn't necessarily a problem (the render additively
    preserves the user's live servers): it's only noted in the detail, it
    doesn't fail the check. A CLI that was never launched (no config file)
    is not a failure: it's undetermined, because that CLI might simply
    never have been used on this machine.
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
            continue  # nothing expected for this CLI on this manifest

        if not path.is_file():
            undetermined_parts.append(f"{cli} (never launched: {path} is missing)")
            continue

        spec = _CLI_RENDER_SPECS[cli]
        try:
            rendered = spec.reader(path)
        except Exception as exc:
            undetermined_parts.append(f"{cli} (unreadable config: {exc})")
            continue

        norm_expected = {spec.normalize(name): name for name in expected}
        missing_norm = set(norm_expected) - rendered
        missing = {norm_expected[n] for n in missing_norm}
        extra = rendered - set(norm_expected)

        if missing:
            broken_parts.append(f"{cli}: missing {', '.join(sorted(missing))}")
        if extra:
            extra_parts.append(f"{cli}: {', '.join(sorted(extra))}")

    if broken_parts:
        return CheckOutcome(
            id="mcp.rendered_configs",
            severity=Severity.BROKEN,
            message="Some CLIs' MCP configs are missing servers expected by the manifest: " + "; ".join(broken_parts),
            action="Run 'agent-sync apply' to regenerate them.",
            remedy=remedy,
            detail=("Extra servers (not in the manifest, additively preserved): " + "; ".join(extra_parts)) if extra_parts else None,
        )
    if undetermined_parts:
        return CheckOutcome(
            id="mcp.rendered_configs",
            severity=Severity.UNDETERMINED,
            message="Some CLIs have not been started yet on this machine, so their MCP config could not be checked: " + "; ".join(undetermined_parts),
        )

    return CheckOutcome(
        id="mcp.rendered_configs",
        severity=Severity.OK,
        message="MCP configuration files generated and aligned for all active CLIs",
    )
