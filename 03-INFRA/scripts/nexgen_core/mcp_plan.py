"""`nexgen mcp plan`: which server lives where, for every CLI, and why.

Read-only. It answers from the same rule the renderer and the gateway use (`mcp_placement`), so what it prints
is what the next render will write and what each CLI's gateway will serve, not a separate opinion about it.
"""
from __future__ import annotations

import json
import os
from typing import Any

from nexgen_core.config import load_mcp_manifest
from nexgen_core.i18n import t
from nexgen_core.mcp_placement import CLIS, DIRECT, GATEWAY, GATEWAY_KIND, gateway_servers_for, place, problems

_SYMBOL = {DIRECT: "direct", GATEWAY_KIND: "gateway", "absent": "-"}


def build(manifest_path) -> dict[str, Any]:
    """The plan as data: rows per server, the gateway's served set per CLI, what would change, what is incoherent."""
    from nexgen_core import mcp_trials

    servers = mcp_trials.overlay(load_mcp_manifest(manifest_path).get("servers", {}))
    rows = []
    for name, srv in servers.items():
        placements = {cli: place(srv, cli) for cli in CLIS}
        reasons = {p.why for p in placements.values() if p.kind != "absent"} or {next(iter(placements.values())).why}
        env = srv.get("require_env")
        rows.append({
            "server": name,
            "cells": {cli: p.kind for cli, p in placements.items()},
            "why": sorted(reasons),
            "needs_env": env if env and not os.environ.get(env) else None,
            "trial": mcp_trials.time_left({"expires": srv["_trial_expires"]}) if "_trial_expires" in srv else None,
        })
    gateway = {cli: sorted(gateway_servers_for(servers, cli)) for cli in CLIS}
    # What the gateway served before it knew its CLI: every `lazy: true` server, to everyone.
    before = sorted(name for name, srv in servers.items() if isinstance(srv, dict) and name != GATEWAY and srv.get("lazy"))
    changes = {}
    for cli in CLIS:
        withheld = sorted(set(before) - set(gateway[cli]))
        added = sorted(set(gateway[cli]) - set(before))
        if withheld or added:
            changes[cli] = {"no_longer_served": withheld, "newly_served": added}
    return {"rows": rows, "gateway": gateway, "changes": changes, "problems": problems(servers)}


def render(plan: dict[str, Any]) -> str:
    width = max([len(r["server"]) for r in plan["rows"]] + [6])
    head = "  " + "server".ljust(width) + "  " + "  ".join(c.ljust(11) for c in CLIS) + "  " + t("why")
    lines = [head, "  " + "-" * (len(head) - 2)]
    for r in plan["rows"]:
        cells = "  ".join(_SYMBOL[r["cells"][cli]].ljust(11) for cli in CLIS)
        note = "; ".join(r["why"]) + (f"  [{t('needs {env}', env=r['needs_env'])}]" if r["needs_env"] else "")
        if r["trial"]:
            note += f"  [{t('trial, {left} left, this machine only', left=r['trial'])}]"
        lines.append("  " + r["server"].ljust(width) + "  " + cells + "  " + note)
    lines.append("")
    lines.append(t("Served by the gateway, per CLI:"))
    for cli in CLIS:
        served = ", ".join(plan["gateway"][cli]) or t("(none)")
        lines.append(f"  {cli}: {served}")
    if plan["changes"]:
        lines.append("")
        lines.append(t("Compared with how the gateway served before it knew its CLI:"))
        for cli, change in plan["changes"].items():
            if change["no_longer_served"]:
                lines.append(f"  {cli}: " + t("no longer served: {names}", names=", ".join(change["no_longer_served"])))
            if change["newly_served"]:
                lines.append(f"  {cli}: " + t("newly served: {names}", names=", ".join(change["newly_served"])))
    if plan["problems"]:
        lines.append("")
        lines.append(t("Incoherent:"))
        lines.extend(f"  ! {p}" for p in plan["problems"])
    return "\n".join(lines)


def main(*, as_json: bool = False) -> int:
    from nexgen_core.renderer import McpRenderer

    renderer = McpRenderer()
    if not renderer.manifest_path.is_file():
        print(t("No MCP manifest at {path}", path=renderer.manifest_path))
        return 1
    plan = build(renderer.manifest_path)
    print(json.dumps(plan, indent=2, ensure_ascii=False) if as_json else render(plan))
    return 1 if plan["problems"] else 0
