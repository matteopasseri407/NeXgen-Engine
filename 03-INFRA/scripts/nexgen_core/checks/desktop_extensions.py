"""Claude Desktop extensions: MCP servers the app runs that no config file the engine reads lists.

The app has two ways to start an MCP server. One is the `mcpServers` table in
`claude_desktop_config.json`, which the renderer manages and the other checks
read. The other is an installed extension (`.mcpb`): its manifest sits in
`extensions-installations.json`, its switch in `Claude Extensions Settings/<id>.json`,
and the app runs it exactly like a table entry. Measured on 2026-11-17: eight
enabled extensions on one machine, none of them visible to the doctor, one of
them a second copy of a server the table also started.

Informational and read-only: an extension is the owner's own install for the
app, not a server the manifest has to declare (owner, 2026-11-17), so the line
only names what runs and never warns or touches it. One the owner wants out of
the list goes into the manifest's `orphans_allowlist` as `claude-desktop:<id>`.
"""
from __future__ import annotations

import json
from pathlib import Path

from nexgen_core.checks.mcp_checks import _orphans_allowlist
from nexgen_core.i18n import t
from nexgen_core.paths import claude_desktop_config
from nexgen_core.report import CheckOutcome, Severity

CHECK_ID = "mcp.desktop_extensions"
INVENTORY = "extensions-installations.json"
SWITCHES = "Claude Extensions Settings"
ALLOW_PREFIX = "claude-desktop:"


def enabled_extensions(app_dir: Path) -> list[tuple[str, str]]:
    """`(id, display name)` of every installed extension whose switch is on, sorted by id.

    An extension with no switch file is off: the app writes the file when it is
    enabled. A switch file that cannot be read counts as on, because a server
    the check cannot rule out is the one worth naming.
    """
    data = json.loads((app_dir / INVENTORY).read_text(encoding="utf-8"))
    found: list[tuple[str, str]] = []
    for ext_id, entry in (data.get("extensions") or {}).items():
        switch = app_dir / SWITCHES / f"{ext_id}.json"
        if not switch.is_file():
            continue
        try:
            enabled = json.loads(switch.read_text(encoding="utf-8")).get("isEnabled") is not False
        except (OSError, ValueError):
            enabled = True
        if enabled:
            manifest = entry.get("manifest") if isinstance(entry, dict) else None
            name = (manifest or {}).get("display_name") or (manifest or {}).get("name") or ext_id
            found.append((ext_id, str(name)))
    return sorted(found)


def check_desktop_extensions(vault_data: Path, home: Path) -> CheckOutcome:
    app_dir = claude_desktop_config(home).parent
    if not (app_dir / INVENTORY).is_file():
        return CheckOutcome(id=CHECK_ID, severity=Severity.OK, message=t("No Claude Desktop extensions installed here"))
    try:
        enabled = enabled_extensions(app_dir)
    except (OSError, ValueError, AttributeError) as exc:
        return CheckOutcome(
            id=CHECK_ID,
            severity=Severity.UNDETERMINED,
            message=t("Could not read the Claude Desktop extensions: {error}", error=exc),
        )
    allowed = _orphans_allowlist(vault_data / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml")
    unknown = [(i, n) for i, n in enabled if f"{ALLOW_PREFIX}{i}" not in allowed and i not in allowed]
    if unknown:
        return CheckOutcome(
            id=CHECK_ID,
            severity=Severity.OK,
            message=t(
                "Claude Desktop runs {count} extensions of its own: {names}",
                count=len(unknown),
                names=", ".join(f"{n} ({i})" for i, n in unknown),
            ),
        )
    return CheckOutcome(
        id=CHECK_ID,
        severity=Severity.OK,
        message=t("Every enabled Claude Desktop extension is acknowledged ({count})", count=len(enabled)),
    )
