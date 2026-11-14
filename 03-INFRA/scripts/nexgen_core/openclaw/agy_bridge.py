"""The Antigravity plugin's MCP bridge, patched so agy receives its connectors once, lazily.

One job: patch the installed `google-antigravity-cli` plugin's `dist/mcp-bridge.js`
so that OpenClaw injects ONLY the loopback server (`openclaw`) and the engine's
`lazy-mcp` gateway into agy's configuration, and strips leftover `openclaw__*` entries
at the start of every run.

Why a patch and not a setting: the plugin merges every connector OpenClaw materialised
for the run into agy's home-level `mcp_config.json` with the `openclaw__` prefix.
This causes agy to launch every connector directly, bypassing on-demand loading and
tool-denial rules. When an agy run is terminated early, cleanup does not run and the
leftovers persist. Filtering the injected servers and cleaning leftovers before merge
ensures agy reaches all connectors lazily through `lazy-mcp` exactly once.
"""
from __future__ import annotations

from pathlib import Path

from nexgen_core.openclaw import plugin_patch
from nexgen_core.openclaw.agy_prereqs import extension_dir
from nexgen_core.openclaw.terms import LOCAL_AGY_BACKEND

#: Marker present only in the patched bridge, ensuring idempotence.
MARKER = "nexgen-mcp-bridge-filter"
#: Tag for backup copies created before applying the patch.
BACKUP_TAG = "agy-mcp-bridge"
#: Relative path of the bridge script inside the extension directory.
BRIDGE_RELATIVE = Path("dist") / "mcp-bridge.js"

#: Replace the unrestricted injected servers with a filter keeping only loopback and gateway.
INJECTED_FILTER = plugin_patch.Edit(
    "    const injected = buildInjectedServers(await readJson(io, params.settingsPath));\n",
    "    /* nexgen-mcp-bridge-filter: bridge only the loopback server and the lazy-mcp gateway */\n"
    "    const _rawInjected = buildInjectedServers(await readJson(io, params.settingsPath));\n"
    "    const injected = Object.fromEntries(Object.entries(_rawInjected).filter(([k]) => {\n"
    '        const name = k.startsWith(OPENCLAW_MCP_SERVER_PREFIX) ? k.slice(OPENCLAW_MCP_SERVER_PREFIX.length) : k;\n'
    '        return name === "openclaw" || name === "lazy-mcp";\n'
    "    }));\n",
)

#: Strip leftover owned entries from existing config before merging the new run's bridge.
START_STRIP = plugin_patch.Edit(
    "    await io.writeFile(params.agyConfigPath, `${JSON.stringify(mergeAgyMcpServers(existing, injected), null, 2)}\\n`);\n",
    "    await io.writeFile(params.agyConfigPath, `${JSON.stringify(mergeAgyMcpServers(stripOwnedServers(existing), injected), null, 2)}\\n`);\n",
)


def bridge_file(state_dir: Path, backend: str = LOCAL_AGY_BACKEND) -> Path:
    """The compiled MCP bridge inside the installed extension."""
    return extension_dir(state_dir, backend) / BRIDGE_RELATIVE


def patched(state_dir: Path, backend: str = LOCAL_AGY_BACKEND) -> bool:
    """Whether the installed bridge filters injected servers to loopback and lazy gateway."""
    try:
        return MARKER in bridge_file(state_dir, backend).read_text(encoding="utf-8")
    except OSError:  # nexgen: allow reason="an absent plugin is simply not patched; the loopback deny then keeps today's narrower rule"
        return False


def apply(state_dir: Path, backend: str = LOCAL_AGY_BACKEND, *, dry_run: bool = False) -> plugin_patch.Patch:
    """Filter the MCP bridge to loopback and lazy gateway, and say what happened."""
    return plugin_patch.apply(
        bridge_file(state_dir, backend),
        [INJECTED_FILTER, START_STRIP],
        marker=MARKER,
        backup_tag=BACKUP_TAG,
        dry_run=dry_run,
    )
