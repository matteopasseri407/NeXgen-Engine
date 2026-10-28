"""Claude Code's own MCP configuration is where a `claude-cli` agent's connectors live.

One job: for a gateway with at least one `claude-cli` agent, make sure Claude Code's
native configuration (`~/.claude.json`) is the file the engine renders, and say in
one line per agent that this is where its connectors come from. It reuses the
renderer `nexgen sync` already runs for the `claude` target -- the same servers, the
same deferral behind ToolSearch -- so there is no second implementation and nothing
about that renderer changes here.

Why this exists: `claude_loopback` takes the connectors off OpenClaw's loopback
server, which is the configuration a plain Claude Code has: the connectors a person
knows are the ones in their own `~/.claude.json`, loaded lazily. Removing them from
the loopback without this step would leave the agent with no connectors at all.

Nothing here writes `alwaysLoad`: the renderer builds every entry from the manifest,
and an entry the engine manages carries only transport, command and environment. A
machine without OpenClaw never reaches this module, and its `~/.claude.json` is
rendered exactly as before.
"""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any, Callable

from nexgen_core.i18n import t
from nexgen_core.openclaw.claude_cli import claude_cli_agents


def announce(agents: Iterable[str], out: Callable[[str], None] = print) -> None:
    """One line per agent, saying where its connectors come from now."""
    for agent_id in agents:
        out(t("The connectors of {agent} come from Claude Code's own configuration.",
              agent=agent_id))


def ensure(
    config: dict[str, Any],
    *,
    home: Path,
    vault_data: Path | None = None,
    engine_root: Path | None = None,
    out: Callable[[str], None] = print,
    dry_run: bool = False,
) -> bool:
    """Render `~/.claude.json` when a `claude-cli` agent needs it; True when nothing is missing.

    The answer is False only when a render was needed and failed, and the reason is
    printed: a setup that stopped the loopback connectors and left the native file
    alone would be the broken half of this change.
    """
    agents = claude_cli_agents(config)
    if not agents:
        return True
    announce(agents, out)
    if dry_run:
        return True
    from nexgen_core.renderer import McpRenderer

    renderer = McpRenderer(vault_data=vault_data, engine_root=engine_root, home=home)
    try:
        ok, message = renderer.render_claude(write=True)
    except (OSError, ValueError) as exc:  # nexgen: allow reason="an unreadable manifest or config is reported as a step that did not land, never a crash after the gateway was already written"
        out(t("Claude Code's own MCP configuration was not rendered: {error}", error=exc))
        return False
    out(message)
    return ok
