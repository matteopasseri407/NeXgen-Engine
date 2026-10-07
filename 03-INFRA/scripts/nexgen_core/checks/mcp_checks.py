"""Checks for MCP server configuration and availability."""
from __future__ import annotations

import json
import logging
import shutil
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from nexgen_core.config import load_mcp_manifest
from nexgen_core.i18n import t
from nexgen_core.jsonc import parse_jsonc
from nexgen_core.paths import (
    antigravity_config,
    antigravity_configs,
    claude_config,
    codex_config,
)
from nexgen_core.renderer import McpRenderer
from nexgen_core.report import CheckOutcome, Severity

logger = logging.getLogger("nexgen.checks.mcp")


def check_mcp_manifest(manifest_path: Path) -> CheckOutcome:
    """Check that mcp/manifest.yaml exists and is valid."""
    if not manifest_path.is_file():
        return CheckOutcome(
            id="mcp.manifest_present",
            severity=Severity.BROKEN,
            message=t("The MCP manifest '{manifest_path}' does not exist.", manifest_path=manifest_path),
            action=t("Create or restore mcp/manifest.yaml from the templates."),
        )

    try:
        data = load_mcp_manifest(manifest_path, strict=True)
        server_count = len(data.get("servers", {}))
        return CheckOutcome(
            id="mcp.manifest_valid",
            severity=Severity.OK,
            message=t("MCP manifest valid with {count} servers declared", count=server_count),
        )
    except Exception as exc:  # noqa: BLE001 - check failure is reported, never raises
        return CheckOutcome(
            id="mcp.manifest_valid",
            severity=Severity.BROKEN,
            message=t("The MCP manifest contains errors: {error}", error=exc),
            action=t("Fix the syntax of mcp/manifest.yaml."),
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
    mcp = data.get("mcp", {}) if isinstance(data, dict) else {}
    # A flat V1 config may still connect via OpenCode's compatibility layer,
    # but it is not aligned with the native V2 renderer contract.
    servers = mcp.get("servers", {}) if isinstance(mcp, dict) else {}
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


def _expected_and_rendered(renderer: McpRenderer, cli: str, path: Path) -> tuple[str | None, set[str], set[str]]:
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
    spec = _CLI_RENDER_SPECS[cli]
    try:
        rendered = spec.reader(path)
    except Exception as exc:  # noqa: BLE001 - check failure is reported, never raises
        return f"{cli} (unreadable config: {exc})", expected, set()
    return None, expected, rendered


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
        "claude": claude_config(home),
        # Canonical first; a machine Antigravity itself configured (never
        # rendered here) may only carry a fan-out copy -- same content,
        # read-only check, so the first existing file wins.
        "antigravity": next(
            (p for p in antigravity_configs(home) if p.is_file()),
            antigravity_config(home),
        ),
        "codex": codex_config(home),
        "opencode": renderer.opencode_config_path(),
    }

    broken_parts: list[str] = []
    undetermined_parts: list[str] = []
    extra_parts: list[str] = []

    for cli, path in cli_paths.items():
        error, expected, rendered = _expected_and_rendered(renderer, cli, path)
        if error:
            if error.startswith("template:"):
                # Apply would fail exactly like this: a fault, not ignorance.
                broken_parts.append(error.removeprefix("template: "))
            else:
                undetermined_parts.append(error)
            continue
        if not expected:
            continue  # nothing expected for this CLI on this manifest

        spec = _CLI_RENDER_SPECS[cli]
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
            message=t(
                "Some CLIs' MCP configs are missing servers expected by the manifest: {parts}",
                parts="; ".join(broken_parts),
            ),
            action=t("Run 'agent-sync apply' to regenerate them."),
            remedy=remedy,
            detail=("Extra servers (not in the manifest, additively preserved): " + "; ".join(extra_parts)) if extra_parts else None,
        )
    if undetermined_parts:
        return CheckOutcome(
            id="mcp.rendered_configs",
            severity=Severity.UNDETERMINED,
            message=t(
                "Some CLIs have not been started yet on this machine, so their MCP config could not be checked: {parts}",
                parts="; ".join(undetermined_parts),
            ),
        )

    return CheckOutcome(
        id="mcp.rendered_configs",
        severity=Severity.OK,
        message=t("MCP configuration files generated and aligned for all active CLIs"),
    )


#: Where each CLI keeps its server table inside its parsed config.
_SERVER_TABLE_PATH: dict[str, tuple[str, ...]] = {
    "claude": ("mcpServers",),
    "antigravity": ("mcpServers",),
    "codex": ("mcp_servers",),
    "opencode": ("mcp", "servers"),
}


def _server_table(cli: str, text: str) -> dict:
    """The server table out of a config's text, in that CLI's own format."""
    if cli == "codex":
        data = tomllib.loads(text)
    elif cli == "opencode":
        data = parse_jsonc(text) if text.strip() else {}
    else:
        data = json.loads(text) if text.strip() else {}
    for key in _SERVER_TABLE_PATH[cli]:
        data = data.get(key, {}) if isinstance(data, dict) else {}
    return data if isinstance(data, dict) else {}


def _changed_server_names(cli: str, old_text: str, new_text: str) -> list[str]:
    """Names whose entry differs between two texts of one config (added, changed or dropped)."""
    old, new = _server_table(cli, old_text), _server_table(cli, new_text)
    return sorted(name for name in old.keys() | new.keys() if old.get(name) != new.get(name))


def check_mcp_content_drift(vault_data: Path, home: Path, engine_root: Path | None = None) -> CheckOutcome:
    """Would `apply` change what a CLI actually launches?

    The names check above passes when a server is listed, whatever its
    command, arguments, environment or timeouts say. A manifest edit that
    changes a server (a new pin, a longer timeout) therefore showed as
    healthy until the next guard cycle happened to rewrite the file. This
    asks the renderer itself, in preview, and reports exactly the servers
    whose rendered entry would change. A CLI that was never launched has no
    file to compare and is left to the names check. The engine root is part
    of what gets rendered (the HTTP bridge's path), so the caller passes the
    one it is running from.
    """
    renderer = McpRenderer(vault_data=vault_data, engine_root=engine_root, home=home)
    paths = {
        "claude": claude_config(home),
        "antigravity": antigravity_config(home),
        "codex": codex_config(home),
        "opencode": renderer.opencode_config_path(),
    }
    drifted: list[str] = []
    skipped: list[str] = []
    for cli, path in paths.items():
        if not path.is_file():
            continue
        try:
            pending = renderer.pending_changes(cli)
            text = pending.get(path)
            if text is None:
                continue
            names = _changed_server_names(cli, path.read_text(encoding="utf-8"), text)
        except Exception as exc:  # noqa: BLE001 - check failure is reported, never raises
            skipped.append(f"{cli} ({exc})")
            continue
        drifted.append(f"{cli}: {', '.join(names)}" if names else f"{cli}: " + t("settings outside the server list"))

    def remedy() -> bool:
        renderer.render_all(write=True)
        return True

    if drifted:
        return CheckOutcome(
            id="mcp.rendered_content",
            severity=Severity.WARN,
            message=t(
                "Some CLI configurations no longer match what the manifest would write: {parts}",
                parts="; ".join(drifted),
            ),
            action=t("Run 'agent-sync apply' to regenerate them (the guard cycle does it on its own too)."),
            remedy=remedy,
        )
    if skipped:
        return CheckOutcome(
            id="mcp.rendered_content",
            severity=Severity.UNDETERMINED,
            message=t("Could not compare these CLI configurations with the manifest: {parts}", parts="; ".join(skipped)),
        )
    return CheckOutcome(
        id="mcp.rendered_content",
        severity=Severity.OK,
        message=t("Every CLI configuration matches what the manifest would write"),
    )


def check_mcp_placement(vault_data: Path) -> CheckOutcome | None:
    """Is every CLI's gateway mounted where servers are routed behind it, and every `exposure` one we know?

    The static half of "the gateway works": it reads only the manifest. `nexgen mcp check` is the live half
    (it starts the gateway and its backends). Returns None where there is no manifest.
    """
    from nexgen_core import mcp_placement

    manifest = vault_data / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    if not manifest.is_file():
        return None
    try:
        servers = load_mcp_manifest(manifest).get("servers", {})
    except Exception as exc:  # noqa: BLE001 - check failure is reported, never raises
        return CheckOutcome(id="mcp.placement", severity=Severity.UNDETERMINED,
                            message=t("The MCP manifest could not be read, so placement was not checked: {error}", error=exc))
    found = mcp_placement.problems(servers)
    if found:
        return CheckOutcome(
            id="mcp.placement",
            severity=Severity.BROKEN,
            message=t("The MCP placement is incoherent: {problems}", problems="; ".join(found)),
            action=t("Run 'nexgen mcp plan' to see it per CLI, then fix the manifest."),
        )
    return CheckOutcome(id="mcp.placement", severity=Severity.OK,
                        message=t("Every CLI's gateway is mounted where servers are routed behind it"))


def _command_resolves(command: str, home: Path) -> bool:
    """Whether a CLI could actually start this command: on PATH, or a file that exists."""
    if not command:
        return False
    expanded = Path(command).expanduser()
    if expanded.is_absolute() or "/" in command or "\\" in command:
        return expanded.is_file()
    return shutil.which(command) is not None


def check_mcp_commands(vault_data: Path, home: Path) -> CheckOutcome:
    """Does every server a CLI is told to start have a program to start?

    A server whose command is not installed does not fail loudly: the CLI
    shows the connector as unavailable and the agent carries on without it.
    The names and content checks both pass on such a config.
    """
    renderer = McpRenderer(vault_data=vault_data, home=home)
    missing: dict[str, set[str]] = {}
    for cli in ("claude", "codex", "antigravity", "opencode"):
        try:
            servers = renderer.load_resolved_servers(cli)
        except Exception:  # noqa: BLE001 - the names check already reports an unresolvable manifest
            continue
        for name, srv in servers.items():
            if srv.get("transport") == "http" or srv.get("url"):
                continue
            command = str(srv.get("command") or "")
            if not _command_resolves(command, home):
                missing.setdefault(name, set()).add(command or "?")
    if missing:
        parts = "; ".join(f"{name} ({', '.join(sorted(cmds))})" for name, cmds in sorted(missing.items()))
        return CheckOutcome(
            id="mcp.commands",
            severity=Severity.WARN,
            message=t("Some MCP servers point at a program that is not installed here: {parts}", parts=parts),
            action=t("Install the missing program, or disable the server in the manifest on this machine."),
        )
    return CheckOutcome(
        id="mcp.commands",
        severity=Severity.OK,
        message=t("Every stdio MCP server has a program to start"),
    )


def check_mcp_deps(manifest_path: Path, state_dir: Path) -> CheckOutcome:
    """Offline-safe check that every server declaring `deps:` is satisfiable.

    Verification only, never installs: npx pins need node on the machine;
    git pins need their pinned workspace already provisioned (the first lazy
    load provisions it). A missing workspace is a WARN, not a failure: lazy
    provisioning is the designed path, and offline is not an incident.
    """
    try:
        data = load_mcp_manifest(manifest_path)
    except Exception:  # noqa: BLE001 - check failure is reported, never raises
        return CheckOutcome(
            id="mcp.deps",
            severity=Severity.UNDETERMINED,
            message=t("The MCP manifest could not be read, so declared dependencies were not checked."),
        )

    from nexgen_core.provision import ensure_deps

    problems: list[str] = []
    for name, srv in (data.get("servers") or {}).items():
        deps = srv.get("deps")
        if not isinstance(deps, dict):
            continue
        _, error = ensure_deps(deps, state_dir, install=False, server=name)
        if error:
            problems.append(f"{name}: {error}")

    if not problems:
        return CheckOutcome(
            id="mcp.deps",
            severity=Severity.OK,
            message=t("All declared MCP dependencies are satisfied on this machine"),
        )
    return CheckOutcome(
        id="mcp.deps",
        severity=Severity.WARN,
        message=t("Some declared MCP dependencies are not provisioned yet: {problems}", problems="; ".join(problems)),
        detail=t("Git-kind dependencies are provisioned lazily at the first server load; run 'agent-sync apply' once (or use the server) to provision them."),
    )


def _orphans_allowlist(manifest_path: Path) -> set[str]:
    """Names the user declared as legitimate outside-the-manifest entries.

    Read from the manifest's own `orphans_allowlist` key, because the
    manifest is the one canonical declaration the vault carries: an allow
    decision belongs next to the declarations it exempts. An entry is either
    a bare server name (exempt on every CLI) or `cli:name` (exempt on that
    CLI only). Anything malformed is ignored with a warning, never fatal:
    an allowlist must not become a second way to break the manifest.
    """
    try:
        data = load_mcp_manifest(manifest_path)
    except Exception:  # noqa: BLE001 - check failure is reported, never raises
        return set()
    raw = data.get("raw") or {}
    entries = raw.get("orphans_allowlist")
    if not isinstance(entries, list):
        return set()
    allowed: set[str] = set()
    for entry in entries:
        if isinstance(entry, str) and entry.strip():
            allowed.add(entry.strip())
        else:
            logger.warning("Ignoring invalid orphans_allowlist entry in %s: %r", manifest_path, entry)
    return allowed


def check_mcp_orphans(vault_data: Path, home: Path) -> CheckOutcome:
    """Servers configured in a CLI but not declared in the manifest.

    WARN-only by design, and never removed: the render additively preserves
    these entries, which means someone put them there outside the manifest,
    and the check's only job is to make that visible (with the config's
    path) instead of letting it drift silently. A legitimate one goes into
    the manifest's `orphans_allowlist`, not into a memory of things to
    ignore.
    """
    renderer = McpRenderer(vault_data=vault_data, home=home)
    manifest_path = vault_data / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    allowed = _orphans_allowlist(manifest_path)

    cli_paths = {
        "claude": claude_config(home),
        "antigravity": next(
            (p for p in antigravity_configs(home) if p.is_file()),
            antigravity_config(home),
        ),
        "codex": codex_config(home),
        "opencode": renderer.opencode_config_path(),
    }

    orphan_parts: list[str] = []
    ignored_count = 0
    for cli, path in cli_paths.items():
        if not path.is_file():
            continue  # never launched: nothing rendered, nothing to report
        error, expected, rendered = _expected_and_rendered(renderer, cli, path)
        if error:
            continue  # the rendered-configs check already reports unreadable configs
        spec = _CLI_RENDER_SPECS[cli]
        norm_expected = {spec.normalize(name) for name in expected}
        orphans = sorted(rendered - norm_expected)
        if not orphans:
            continue
        visible = []
        for name in orphans:
            # Codex renders section names with underscores; the allowlist
            # speaks the manifest's names. Both spellings match, plus the
            # bare form (exempt on every CLI).
            variants = {name, name.replace("-", "_"), name.replace("_", "-")}
            if any(v in allowed or f"{cli}:{v}" in allowed for v in variants):
                ignored_count += 1
                continue
            visible.append(name)
        if visible:
            orphan_parts.append(f"{cli} ({path}): {', '.join(visible)}")

    if orphan_parts:
        return CheckOutcome(
            id="mcp.orphans",
            severity=Severity.WARN,
            message=t("MCP servers configured outside the manifest (kept, never removed): {parts}", parts="; ".join(orphan_parts)),
            action=t("Adopt them with 'nexgen import --from <cli>' or declare them in mcp/manifest.yaml; add them to orphans_allowlist if they are legitimate."),
        )
    if ignored_count:
        return CheckOutcome(
            id="mcp.orphans",
            severity=Severity.OK,
            message=t("No unacknowledged MCP orphans ({count} allowlisted)", count=ignored_count),
        )
    return CheckOutcome(
        id="mcp.orphans",
        severity=Severity.OK,
        message=t("No MCP servers configured outside the manifest"),
    )
