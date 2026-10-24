"""What a local model, and the supervisor agent, may do inside OpenClaw.

One job: the tools block and the two allowlists that decide what a model can call —
the cage around every local model, the writer tools derived from one table, and the
permission the supervisor agent needs to drive the supervision. These are the rules a
person is most likely to want to read, and they were part of `profile.py` until that
file was full; nothing here reads a file or the clock, exactly like the module it left.

Three of the rules are not preferences, and the reason is beside each one: a local model
never writes, an agent's own `allow` list is never overridden, and a plugin tool marked
`optional` is not offered to anyone until an operator names it.
"""
from __future__ import annotations

from typing import Any

from nexgen_core.openclaw.local_models import LocalModel

#: What a local model may still do once caged: read the conversation, look at an
#: image, read a PDF, speak, and search the Vault. Nothing that writes.
LOCAL_SAFE_TOOLS = ("read", "view_image", "pdf", "tts", "memory_search", "memory_get")

#: Denied to every local model. The groups are whole families of capability, so
#: a new tool in one of them is covered before anyone has to notice it exists.
LOCAL_DENY = (
    "group:web", "group:runtime", "group:ui", "group:nodes", "group:automation",
    "write", "edit", "apply_patch", "skill_workshop",
    "playwright__*", "lazy-mcp__lazy_mutate",
)

#: The agent that supervises work, and the plugin tools that change the engine's state.
#: The plugin registers those as `optional`, which in OpenClaw means "not exposed until an
#: operator allowlists this exact name": this list, written on one agent, is that operator.
#: The names are also in the plugin manifest and in `sup-tools.mjs`, and a test fails when
#: the three drift apart.
SUPERVISOR_AGENT = "capo"
SUPERVISOR_TOOLS = (
    "nexgen_sup_start",
    "nexgen_sup_unit",
    "nexgen_sup_run",
    "nexgen_sup_collect",
    "nexgen_sup_review",
    "nexgen_sup_publish",
)

#: MCP servers whose tools write, and the names of those tools, expressed once as
#: data. OpenClaw names them `server__tool`, so a pattern here reads as "no
#: workflow creation, no note writing, no upload" without enumerating every tool.
#: `firecrawl` is here for the tools that fetch or drive a page: search and the
#: plain reads stay allowed, scraping and browsing do not.
WRITE_TOOL_TABLE: dict[str, tuple[str, ...]] = {
    "n8n-mcp": (
        "create_*", "delete_*", "update_*", "publish_*", "unpublish_*",
        "execute_*", "test_*", "validate_*", "mutate_*", "import_*", "archive_*",
    ),
    "vault-library": ("create_note", "append_note", "update_note", "update_section"),
    "drive": ("drive_propose_upload", "drive_confirm_upload"),
    "firecrawl": (
        "*scrape*", "*map*", "*extract*", "*crawl*", "*agent*", "*browser*", "*interact*",
    ),
}


def denied_tools() -> tuple[str, ...]:
    """Every tool pattern a local model may not call, base families plus writer tools."""
    patterns = list(LOCAL_DENY)
    for server, names in WRITE_TOOL_TABLE.items():
        patterns.extend(f"{server}__{name}" for name in names)
    return tuple(patterns)


def cage_block(models: tuple[LocalModel, ...]) -> dict[str, Any]:
    """The cage around each local model: read and speak, never write."""
    deny = list(denied_tools())
    return {
        model.ref: {
            "profile": "messaging",
            "alsoAllow": list(LOCAL_SAFE_TOOLS),
            "deny": list(deny),
        }
        for model in models
    }


def person_allow_list(current: dict[str, Any] | None) -> bool:
    """Whether the supervisor agent carries its own `allow` list in the file in force.

    OpenClaw rejects `allow` and `alsoAllow` in the same scope, so a person who wrote an
    allowlist for that agent keeps it: the engine says what to add there instead of writing
    a file OpenClaw would refuse to load.
    """
    entries = current.get("agents", {}).get("entries") if isinstance(current, dict) else None
    entry = entries.get(SUPERVISOR_AGENT) if isinstance(entries, dict) else None
    tools = entry.get("tools") if isinstance(entry, dict) else None
    return isinstance(tools, dict) and isinstance(tools.get("allow"), list)


def allow_supervisor_tools(entries: dict[str, Any], current: dict[str, Any] | None) -> None:
    """Let the supervisor agent, and only it, drive the supervision.

    The plugin registers those tools `optional`: without a name in an allowlist they are not
    offered to any model. This writes that allowlist on the one agent that supervises.
    """
    if person_allow_list(current):
        return
    entry = entries.get(SUPERVISOR_AGENT)
    if not isinstance(entry, dict):
        return
    tools = entry.get("tools") if isinstance(entry.get("tools"), dict) else {}
    entry["tools"] = {**tools, "alsoAllow": sorted(SUPERVISOR_TOOLS)}


def tools_block(models: tuple[LocalModel, ...], partners: dict[str, list[str]]) -> dict[str, Any]:
    """The tools block: a fixed small list, the exec policy, the cage, and who may message whom."""
    tools: dict[str, Any] = {
        # With small models, tool search fails; a fixed small list works.
        "toolSearch": False,
        "exec": {"security": "allowlist", "ask": "on-miss"},
        "byProvider": cage_block(models),
    }
    if partners:
        tools["agentToAgent"] = {"enabled": True, "allow": sorted(partners)}
    return tools
