"""How to read the server names a CLI already has in its own native config.

Every CLI writes its MCP section in its own shape and its own file, and the
drift and orphan checks must agree on what "already rendered" means, or the two
report opposite verdicts about the same machine. One reader per dialect and one
table, so a new dialect is added once instead of in every check that compares
`nexgen`'s plan with what is on disk.
"""
from __future__ import annotations

import json
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from nexgen_core.jsonc import parse_jsonc

if TYPE_CHECKING:  # the renderer imports this package, so only the type is needed here
    from nexgen_core.renderer import McpRenderer


def rendered_names_claude_style(path: Path) -> set[str]:
    """Server names for Claude and Antigravity: both write `mcpServers`
    in plain JSON."""
    data = json.loads(path.read_text(encoding="utf-8"))
    servers = data.get("mcpServers", {})
    return set(servers.keys()) if isinstance(servers, dict) else set()


def rendered_names_opencode(path: Path) -> set[str]:
    raw = path.read_text(encoding="utf-8")
    data = parse_jsonc(raw) if path.suffix == ".jsonc" else json.loads(raw)
    mcp = data.get("mcp", {}) if isinstance(data, dict) else {}
    # A flat V1 config may still connect via OpenCode's compatibility layer,
    # but it is not aligned with the native V2 renderer contract.
    servers = mcp.get("servers", {}) if isinstance(mcp, dict) else {}
    return set(servers.keys()) if isinstance(servers, dict) else set()


def rendered_names_codex(path: Path) -> set[str]:
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    servers = data.get("mcp_servers", {})
    return set(servers.keys()) if isinstance(servers, dict) else set()


def rendered_names_openclaw(path: Path) -> set[str]:
    """OpenClaw keeps its servers under `mcp.servers`, in the same file as its own settings."""
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    mcp = data.get("mcp", {}) if isinstance(data, dict) else {}
    servers = mcp.get("servers", {}) if isinstance(mcp, dict) else {}
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
CLI_RENDER_SPECS: dict[str, _CliRenderSpec] = {
    "claude": _CliRenderSpec(rendered_names_claude_style, _identity),
    "claude-desktop": _CliRenderSpec(rendered_names_claude_style, _identity),
    "antigravity": _CliRenderSpec(rendered_names_claude_style, _identity),
    "codex": _CliRenderSpec(rendered_names_codex, _codex_section_name),
    "opencode": _CliRenderSpec(rendered_names_opencode, _identity),
    "openclaw": _CliRenderSpec(rendered_names_openclaw, _identity),
}


def expected_and_rendered(renderer: McpRenderer, cli: str, path: Path) -> tuple[str | None, set[str], set[str]]:
    """The expected and rendered server-name sets for one CLI.

    Shared by the drift check and the orphan check so the two cannot
    disagree about what "rendered" means. The string result is a reason the
    sets are unknowable: prefixed "template:" for a manifest the renderer
    cannot resolve (apply would fail the same way — that is drift, not
    ignorance), plain for an unreadable or never-launched config.
    """
    try:
        expected = set(renderer.load_resolved_servers(cli).keys())
    except Exception as exc:  # noqa: BLE001 - check failure is reported, never raises
        return f"template: the manifest cannot be rendered for {cli} ({exc})", set(), set()
    if not path.is_file():
        return f"{cli} (never launched: {path} is missing)", expected, set()
    spec = CLI_RENDER_SPECS[cli]
    try:
        rendered = spec.reader(path)
    except Exception as exc:  # noqa: BLE001 - check failure is reported, never raises
        return f"{cli} (unreadable config: {exc})", expected, set()
    return None, expected, rendered
