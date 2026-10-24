"""Does OpenClaw receive from the engine exactly what Claude Code receives?

One plugin, two hosts, the same layers on both: MCP servers, instructions, skills, the guardrail,
the vault briefing and the permission posture. Each layer is compared as a fact the
engine can read back from disk -- what is registered, deposited or switched on -- never as an
intention. An aligned layer is a PASS; a layer one host has and the other lacks is a WARN that
names the gap and the one command that closes it.

Two rules shape the whole file:

- Silence on a machine that never chose both CLIs. Without Claude Code's settings and
  OpenClaw's configuration there is nothing to compare, and `nexgen doctor` must look exactly as
  it did before this check existed.
- Names only, never secrets. An MCP server is compared by its name in `~/.claude.json` and
  `openclaw.json`; the `env` blocks that may carry a token are never read, printed or kept.

The layer functions are pure readers: this module opens files and returns outcomes, and has no
remedy of its own. `nexgen sync` is the action every WARN points at, because that is the one
phase that writes both sides.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nexgen_core.i18n import t
from nexgen_core.paths import claude_settings, openclaw_config, resolve_vault_data
from nexgen_core.report import CheckOutcome, Severity

#: The hook that re-grounds a resumed session in Claude. Its registration is the fact OpenClaw
#: follows; nothing here writes it.
CHECKPOINT = "claude-vault-checkpoint.mjs"
GUARDRAIL_ADAPTER = "claude-guardrail-adapter.mjs"
SYNC_ACTION = t("Run 'nexgen sync': it writes both sides from the same manifest.")

#: `~/.agents/skills` is one folder with two readers: the engine's shared catalog, which is
#: where OpenClaw looks, and the native view of OpenCode. Claude keeps its own copy.
SHARED_SKILLS = (".agents", "skills")
CLAUDE_SKILLS = (".claude", "skills")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # nexgen: allow reason="an unreadable config is 'nothing to compare', and the file's own check already reports the damage"
        return {}
    return data if isinstance(data, dict) else {}


def _hook_commands(settings: dict[str, Any], event: str) -> list[str]:
    """Every command registered on `event`, in Claude's own `hooks` shape."""
    hooks = settings.get("hooks") if isinstance(settings.get("hooks"), dict) else {}
    groups = hooks.get(event) if isinstance(hooks.get(event), list) else []
    commands: list[str] = []
    for group in groups:
        inner = group.get("hooks") if isinstance(group, dict) else None
        for item in inner if isinstance(inner, list) else []:
            command = item.get("command") if isinstance(item, dict) else None
            if isinstance(command, str):
                commands.append(command)
    return commands


def _has_hook(settings: dict[str, Any], event: str, needle: str) -> bool:
    return any(needle in command for command in _hook_commands(settings, event))


def _outcome(layer: str, aligned: bool, message: str, action: str | None = None) -> CheckOutcome:
    return CheckOutcome(
        id=f"openclaw.parity.{layer}",
        severity=Severity.OK if aligned else Severity.WARN,
        message=message,
        action=None if aligned else action,
    )


def _both_or_neither(layer: str, claude_has: bool, openclaw_has: bool, what: str, action: str) -> CheckOutcome:
    """A layer that is either on for both or off for both; one-sided is the divergence."""
    if claude_has == openclaw_has:
        state = t("both") if claude_has else t("neither")
        return _outcome(layer, True, t("{what}: {state}", what=what, state=state))
    side = t("Claude Code") if claude_has else t("OpenClaw")
    missing = t("OpenClaw") if claude_has else t("Claude Code")
    return _outcome(
        layer, False,
        t("{what}: {side} has it, {missing} does not", what=what, side=side, missing=missing),
        action,
    )


# ---------------------------------------------------------------- the machine


def _expected_mcp(vault_data: Path) -> tuple[set[str], set[str]] | None:
    """The manifest servers each side mounts directly, or None without a manifest."""
    from nexgen_core.config import load_mcp_manifest
    from nexgen_core.errors import NexgenError
    from nexgen_core.mcp_placement import DIRECT, place

    path = vault_data / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    if not path.is_file():
        return None
    try:
        data = load_mcp_manifest(path)
    except (NexgenError, OSError):  # nexgen: allow reason="a broken manifest is reported by mcp.manifest; this layer compares nothing instead of crashing the doctor"
        return None
    servers = data.get("servers") if isinstance(data.get("servers"), dict) else {}
    return (
        {name for name, srv in servers.items() if isinstance(srv, dict) and place(srv, "claude").kind == DIRECT},
        {name for name, srv in servers.items() if isinstance(srv, dict) and place(srv, "openclaw").kind == DIRECT},
    )


def _expected_skills(vault_data: Path) -> set[str] | None:
    """The manifest skills both CLIs should hold, or None without a manifest.

    OpenClaw and OpenCode read the same `~/.agents/skills` catalog, so a skill
    targeted only at OpenCode is visible to OpenClaw too. Requiring an explicit
    `openclaw` target counted zero on a real machine where OpenClaw saw 21.
    """
    from nexgen_core.config import load_skills_manifest
    from nexgen_core.errors import NexgenError
    from nexgen_core.paths import skills_manifest

    path = skills_manifest(vault_data)
    if not path.is_file():
        return None
    try:
        data = load_skills_manifest(path)
    except (NexgenError, OSError):  # nexgen: allow reason="a broken manifest is reported by skills.manifest; this layer compares nothing instead of crashing the doctor"
        return None
    skills = data.get("skills") if isinstance(data.get("skills"), dict) else {}
    default = ["claude", "codex", "antigravity", "opencode"]
    expected: set[str] = set()
    for name, entry in skills.items():
        if not isinstance(entry, dict):
            continue
        targets = set(entry.get("targets", default))
        if "claude" in targets and targets & {"openclaw", "opencode"}:
            expected.add(name)
    return expected


def _openclaw_block_present(home: Path, config_path: Path, vault_data: Path) -> bool:
    """Does every existing agent workspace carry the engine's complete block?"""
    from nexgen_core.guard_instructions import openclaw_file_state, openclaw_instruction_block, openclaw_workspaces
    from nexgen_core.paths import canonical_instructions

    workspaces = openclaw_workspaces(config_path, home)
    if not workspaces:
        return False
    block = openclaw_instruction_block(canonical_instructions(vault_data))
    for workspace in workspaces:
        if not workspace.is_dir():
            return False  # declared, not created: the agent has no instructions yet
        try:
            text = (workspace / "AGENTS.md").read_text(encoding="utf-8")
        except OSError:  # nexgen: allow reason="a workspace whose AGENTS.md cannot be read is not aligned, and instructions.openclaw reports the path"
            return False
        if openclaw_file_state(text, block) != "ok":
            return False
    return True


# ------------------------------------------------------------------- layers


def _layer_mcp(claude: dict, openclaw: dict, home: Path, vault_data: Path) -> CheckOutcome | None:
    expected = _expected_mcp(vault_data)
    if expected is None:
        return None
    both = expected[0] & expected[1]
    live_claude = set(claude["mcp"])
    live_openclaw = set(openclaw["servers"])
    on_claude = both & live_claude
    on_openclaw = both & live_openclaw
    if on_claude == on_openclaw:
        return _outcome("mcp", True, t("MCP: both CLIs mount the same {count} manifest servers.", count=len(on_claude)))
    missing_claude = sorted(on_openclaw - on_claude)
    missing_openclaw = sorted(on_claude - on_openclaw)
    gaps: list[str] = []
    if missing_openclaw:
        gaps.append(t("OpenClaw is missing {names}", names=", ".join(missing_openclaw)))
    if missing_claude:
        gaps.append(t("Claude Code is missing {names}", names=", ".join(missing_claude)))
    return _outcome("mcp", False, t("MCP servers diverge: {gaps}.", gaps="; ".join(gaps)), SYNC_ACTION)


def _layer_instructions(claude: dict, openclaw: dict, home: Path, vault_data: Path) -> CheckOutcome:
    from nexgen_core.paths import canonical_instructions

    canon = canonical_instructions(vault_data)
    pointer = home / "CLAUDE.md"
    claude_ok = pointer.is_file()
    if claude_ok and canon.is_file():
        try:
            claude_ok = str(canon) in pointer.read_text(encoding="utf-8")
        except OSError:
            claude_ok = False
    openclaw_ok = _openclaw_block_present(home, openclaw["path"], vault_data)
    return _both_or_neither(
        "instructions", claude_ok, openclaw_ok, t("instructions"), SYNC_ACTION,
    )


def _layer_skills(claude: dict, openclaw: dict, home: Path, vault_data: Path) -> CheckOutcome | None:
    expected = _expected_skills(vault_data)
    if expected is None:
        return None
    on_claude = {name for name in expected if (home.joinpath(*CLAUDE_SKILLS) / name).exists()}
    on_openclaw = {name for name in expected if (home.joinpath(*SHARED_SKILLS) / name).exists()}
    if on_claude == on_openclaw:
        return _outcome("skills", True, t("Skills: both CLIs see the same {count} shared skills.", count=len(on_claude)))
    missing_claude = sorted(on_openclaw - on_claude)
    missing_openclaw = sorted(on_claude - on_openclaw)
    gaps: list[str] = []
    if missing_openclaw:
        gaps.append(t("OpenClaw is missing {names}", names=", ".join(missing_openclaw)))
    if missing_claude:
        gaps.append(t("Claude Code is missing {names}", names=", ".join(missing_claude)))
    return _outcome("skills", False, t("Skills diverge: {gaps}.", gaps="; ".join(gaps)), SYNC_ACTION)


def _openclaw_guardrail_live(openclaw: dict) -> bool:
    """The guardrail OpenClaw will really load: deposited whole, and enabled."""
    from nexgen_core.checks.guardrail_checks import openclaw_plugin_disabled_reason
    from nexgen_core.runtimes.openclaw import PLUGIN_ID

    plugin_dir = openclaw["plugin_dir"]
    for name in ("nexgen-guardrail.config.json", "index.mjs", "guardrail.mjs"):
        if not (plugin_dir / name).is_file():
            return False
    return openclaw_plugin_disabled_reason(openclaw["config"], PLUGIN_ID) is None


def _layer_guardrail(claude: dict, openclaw: dict, home: Path, vault_data: Path) -> CheckOutcome:
    claude_ok = _has_hook(claude["settings"], "PreToolUse", GUARDRAIL_ADAPTER)
    openclaw_ok = _openclaw_guardrail_live(openclaw)
    return _both_or_neither(
        "guardrail", claude_ok, openclaw_ok, t("the guardrail"),
        t("Run 'nexgen sync' and then 'nexgen openclaw setup' (enabling the plugin needs the gateway stopped)."),
    )


def _layer_briefing(claude: dict, openclaw: dict, home: Path, vault_data: Path) -> CheckOutcome:
    from nexgen_core.runtimes.claude import checkpoint_registered

    claude_ok = checkpoint_registered(home)
    briefing = openclaw["plugin_config"].get("briefing")
    openclaw_ok = isinstance(briefing, dict) and briefing.get("enabled") is True
    return _both_or_neither(
        "briefing", claude_ok, openclaw_ok, t("the vault briefing"),
        t("Wire ~/.claude/{hook} into SessionStart, or run 'nexgen sync': OpenClaw follows Claude's own "
          "registration, and its plugin is deposited by the permissions phase when a guardrail hook is declared.",
          hook=CHECKPOINT),
    )


def _layer_posture(claude: dict, openclaw: dict, home: Path, vault_data: Path) -> CheckOutcome:
    from nexgen_core.runtimes.claude import _CLAUDE_TO_POSTURE

    permissions = claude["settings"].get("permissions") if isinstance(claude["settings"].get("permissions"), dict) else {}
    claude_posture = _CLAUDE_TO_POSTURE.get(permissions.get("defaultMode"))
    openclaw_posture = openclaw["plugin_config"].get("posture")
    openclaw_posture = openclaw_posture if isinstance(openclaw_posture, str) else None
    if claude_posture == openclaw_posture:
        state = claude_posture or t("none (no manifest posture)")
        return _outcome("posture", True, t("Posture: both CLIs run '{state}'.", state=state))
    return _outcome(
        "posture", False,
        t("Posture diverges: Claude Code runs '{claude}', OpenClaw runs '{openclaw}'.",
          claude=claude_posture or t("none"), openclaw=openclaw_posture or t("none")),
        t("Declare 'openclaw' next to 'claude' under posture in permissions/manifest.yaml, then run 'nexgen sync'."),
    )


# ---------------------------------------------------------------- entry point

_LAYERS = (
    _layer_mcp,
    _layer_instructions,
    _layer_skills,
    _layer_guardrail,
    _layer_briefing,
    _layer_posture,
)


def check_openclaw_parity(home: Path, vault_data: Path | None = None) -> list[CheckOutcome]:
    """One outcome per aligned or diverging layer; nothing at all on a machine without both CLIs."""
    from nexgen_core.runtimes.openclaw import PLUGIN_ID

    settings_path = claude_settings(home)
    config_path = openclaw_config(home)
    if not settings_path.is_file() or not config_path.is_file():
        return []
    data = resolve_vault_data(home, vault_data)
    plugin_dir = config_path.parent / "extensions" / PLUGIN_ID
    openclaw = {
        "path": config_path,
        "config": _read_json(config_path),
        "servers": _live_openclaw_servers(config_path),
        "plugin_dir": plugin_dir,
        "plugin_config": _read_json(plugin_dir / "nexgen.config.json"),
    }
    claude = {"settings": _read_json(settings_path), "mcp": _live_claude_servers(home)}
    outcomes: list[CheckOutcome] = []
    for layer in _LAYERS:
        outcome = layer(claude, openclaw, home, data)
        if outcome is not None:
            outcomes.append(outcome)
    return outcomes


def _live_claude_servers(home: Path) -> set[str]:
    """The names Claude mounts, never their contents: an `env` block can carry a token."""
    data = _read_json(home / ".claude.json")
    servers = data.get("mcpServers") if isinstance(data.get("mcpServers"), dict) else {}
    return set(servers)


def _live_openclaw_servers(config_path: Path) -> set[str]:
    data = _read_json(config_path)
    mcp = data.get("mcp") if isinstance(data.get("mcp"), dict) else {}
    servers = mcp.get("servers") if isinstance(mcp.get("servers"), dict) else {}
    return set(servers)
