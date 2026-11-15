"""The words `nexgen info` puts beside an item about its update: what moved, what was held and why.

One job: own the sentences of the third-party part of the dashboard, separated from the layout that
positions them. `info` decides what to list and where; this module decides how an update reads, and
nothing else. It was lifted out of `tools/info.py` when that file passed its size ceiling, which is
also why the held reason and the newest known version live here instead of in four sibling helpers.
"""
from __future__ import annotations

from typing import Any

from nexgen_core import extensions
from nexgen_core.i18n import t

#: Why a server is mounted nowhere, in words (the placement rule's own reasons are for `nexgen mcp plan`).
_WHY_OFF = {
    "not in targets": lambda: t("no CLI is in its targets"),
    "disabled": lambda: t("switched off in the manifest"),
    "inert (not core, not enabled)": lambda: t("neither core nor switched on"),
}

#: How loudly one footer line reads: something moved, or a hint about what to do.
ALERT, HINT = "alert", "hint"


def _stale_line(update: dict[str, Any]) -> str:
    """`upstream X available (pinned Y)`, plus whether the guardian cleared it or holds it and why."""
    line = t("upstream {new} available (pinned {old})",
             new=update.get("upstream") or "?", old=update.get("pinned") or "?")
    if update.get("verdict") == "held":
        line += " — " + t("held: {why}", why=update.get("plain") or t("read the changelog first"))
    elif update.get("verdict") == "ready":
        line += " — " + t("cleared, ready to raise")
    return line


def mcp_notes(row: dict[str, Any]) -> list[str]:
    """What to say under one MCP server: what moved, what is hidden, and why it is mounted nowhere."""
    notes = []
    update = row["update"]
    if update.get("state") == "stale":
        notes.append(_stale_line(update))
    if update.get("deprecated"):
        notes.append(t("its publisher withdrew support for this version: {why}", why=update["deprecated"]))
    if row["pin_state"] == "unpinned":
        notes.append(t("not pinned: runs whatever the registry serves today, and nothing watches it"))
    if row["hidden_tools"]:
        notes.append(t("{count} tools hidden", count=row["hidden_tools"]))
    if row["only_tools"]:
        notes.append(t("only {count} tools exposed", count=row["only_tools"]))
    if row["where"] == "off" and row["why_off"]:
        notes.append(t("not mounted anywhere: {why}", why=_WHY_OFF.get(row["why_off"], lambda: row["why_off"])()))
    return notes


def module_notes(row: dict[str, Any]) -> list[str]:
    """What to say under one component of a module: what moved, and what its publisher withdrew."""
    notes = []
    update = row["update"]
    if update.get("state") == "stale":
        if row["kind"] == "docker-image":
            # The engine's shipped default, not what any one server runs: say so instead of "you have".
            line = t("upstream {new} available (the engine's default image is {old}; the server running it may differ)",
                     new=update.get("upstream") or "?", old=update.get("pinned") or "?")
        else:
            line = _stale_line(update)
        notes.append(line)
    if update.get("deprecated"):
        notes.append(t("its publisher withdrew support for this version: {why}", why=update["deprecated"]))
    return notes


def skill_notes(row: dict[str, Any]) -> list[str]:
    """What to say under one skill: what moved and why it stays, and whether a Vault copy is what runs."""
    notes = []
    update = row["update"]
    if update.get("state") == "stale":
        notes.append(_stale_line(update))
    if update.get("deprecated"):
        notes.append(t("its publisher withdrew support for this version: {why}", why=update["deprecated"]))
    if row["engine_copy"] == "differs":
        notes.append(t("your Vault's copy differs from the engine's, and the copy is what runs"))
    elif row["engine_copy"] == "same":
        notes.append(t("a frozen copy in your Vault, identical to the engine's today: nexgen skills adopt"))
    if not row["in_library"]:
        notes.append(t("declared but not materialized on this machine yet"))
    return notes


def update_footer(ext: dict[str, Any]) -> list[tuple[str, str]]:
    """The lines under everything, each with how loudly it reads: `alert` or `hint`.

    It names both commands on purpose. `nexgen skills bump` applies what the guardian cleared, and on
    a machine where everything is held it applies nothing — so the line that explains each item has
    to be beside it, or the recommendation reads as a command that does not work.
    """
    out: list[tuple[str, str]] = []
    moved = extensions.updates(ext)
    age = extensions.age_text(ext.get("upstream_checked_at"))
    if moved:
        out.append((t("Newer upstream versions exist for third-party items: {count} (checked {age} ago).",
                      count=len(moved), age=age or "?"), ALERT))
        out.append((t("nexgen skills bump  (or: nexgen mcp bump)  raises the ones the guardian cleared, tries each server and puts the old pin back if it fails."), HINT))
        out.append((t("nexgen thirdparty status  says, item by item, what updates by itself and what waits for you."), HINT))
    elif age is None and any(r["update"].get("state") == "unknown"
                             for r in (ext.get("mcp") or []) + (ext.get("skills") or [])):
        out.append((t("Upstream versions not checked on this machine yet: the hourly watch will, or run nexgen skills bump."), HINT))
    else:
        out.append((t("Third-party pins are current (checked {age} ago).", age=age or "?"), HINT))
    withdrawn = extensions.deprecations(ext)
    if withdrawn:
        out.append((t("Support withdrawn by the publisher: {names}", names=", ".join(d["name"] for d in withdrawn)), ALERT))
    return out
