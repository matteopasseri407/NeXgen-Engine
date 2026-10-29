"""The render an adoption runs at once, so a connector is usable this minute.

One job: after a pass has added at least one MCP server to the manifest, run the
render `nexgen sync` runs and say what landed. The render is the one the engine
already owns (`McpRenderer.render_all`), not a second copy of it: the lazy rule
stays in `mcp_placement`, so an adopted server arrives behind `lazy-mcp` in
Claude Code, Codex and OpenCode, and in OpenClaw's own file for `agy`, exactly as
a server added by hand would.

A failure never undoes the adoption: the row is already in the manifest and the
next scheduled pass renders it again. The person is told anyway, because a
connector that was adopted and not mounted looks like an adoption that did
nothing.
"""
from __future__ import annotations

from pathlib import Path

from nexgen_core.i18n import t


def render_adopted_servers(
    *,
    home: Path,
    vault_data: Path | None = None,
    engine_root: Path | None = None,
) -> tuple[bool, str]:
    """Render every CLI from the manifest now; False and the reason when some did not land."""
    from nexgen_core.renderer import McpRenderer

    try:
        renderer = McpRenderer(vault_data=vault_data, engine_root=engine_root, home=home)
        results = renderer.render_all(write=True)
    except Exception as exc:  # noqa: BLE001 - nexgen: allow reason="a render that cannot start is reported, and the adoption it follows stays written"
        return False, t(
            "The adopted MCP servers are in the manifest, but the CLIs were not rewritten ({error}). "
            "The next pass will retry.",
            error=exc,
        )
    failed = sorted(cli for cli, ok in results.items() if not ok)
    if failed:
        details = ", ".join(
            f"{cli} ({renderer.last_errors[cli]})" if renderer.last_errors.get(cli) else cli
            for cli in failed
        )
        return False, t(
            "The adopted MCP servers are in the manifest, but these CLIs were not rewritten: {clis}. "
            "The next pass will retry.",
            clis=details,
        )
    return True, t("MCP configurations regenerated for every CLI")
