"""Loopback deny guard for OpenClaw documents.

nexgen: one-job reason="ensure an OpenClaw document carries both agent and provider loopback denies whenever connectors are mounted"
Whoever mounts a connector in OpenClaw must also deny it on the loopback for
agents and providers routed to native CLI runtimes (Claude Code, agy). This
module unifies the per-agent deny and the per-provider deny into a single pure
transformation, called by both `mcp_render.openclaw` (sync) and `openclaw.plan`
(setup).
"""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from nexgen_core.openclaw import claude_loopback, loopback_provider_policy
from nexgen_core.openclaw.claude_loopback import NOT_A_CONNECTOR
from nexgen_core.openclaw.native_connectors import loopback_deny
from nexgen_core.openclaw.setup_write import merged_document


def document_connector_names(document: dict[str, Any] | None) -> list[str]:
    """Every connector mounted under `mcp.servers` in the document, excluding internal gateways.

    nexgen: one-job reason="extract mounted connector names from an OpenClaw document without hardcoding server lists"
    """
    if not isinstance(document, dict):
        return []
    mcp = document.get("mcp") if isinstance(document, dict) else None
    servers = mcp.get("servers") if isinstance(mcp, dict) else None
    if not isinstance(servers, dict):
        return []
    return sorted(str(name) for name in servers if str(name) and str(name) not in NOT_A_CONNECTOR)


#: Alias for document_connector_names
connector_names = document_connector_names


def apply_loopback_guard(
    document: dict[str, Any],
    connectors: Iterable[str] | None = None,
    home: Path | None = None,
    *,
    current: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return document with per-agent and per-provider loopback denies applied.

    nexgen: one-job reason="apply both agent and provider loopback tool denies to an OpenClaw document"
    """
    effective_doc = merged_document(current, document) if current is not None else document
    if connectors is None:
        raw_connectors = document_connector_names(effective_doc)
    else:
        raw_connectors = [str(n) for n in connectors]

    valid_connectors = [name for name in raw_connectors if name and name not in NOT_A_CONNECTOR]
    deny = loopback_deny(effective_doc, valid_connectors, home)

    updated = document
    if deny.claude:
        updated = claude_loopback.apply(updated, valid_connectors, deny.claude)
    if deny.agy:
        updated = claude_loopback.apply(updated, deny.agy_connectors, deny.agy)

    return loopback_provider_policy.apply(
        updated,
        valid_connectors,
        deny.agy_connectors,
        current=current,
    )
