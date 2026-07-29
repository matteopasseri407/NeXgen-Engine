"""CI smoke test for the bundled vault-library MCP server.

Run by .github/workflows/ci.yml (vault-mcp-smoke job) against a container
started from this directory's Dockerfile, mounted on a fixture vault +
bare repo. Exercises the real write path over streamable-http MCP:

    list tools -> create_note -> read_note -> update_note (hash guard)
    -> update_section (per-section hash guard, stale hash refused)

plus one negative check (a write into 99-SECRETS must be refused). The
surrounding job verifies the produced Git commits afterwards.

Needs: pip install "mcp>=2.0,<3". Env: SMOKE_URL, SMOKE_TOKEN.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

URL = os.environ.get("SMOKE_URL", "http://127.0.0.1:8081/mcp")
TOKEN = os.environ["SMOKE_TOKEN"]

REQUIRED_TOOLS = {
    "get_start_here",
    "read_note",
    "search_notes",
    "list_related",
    "recent_activity",
    "create_note",
    "append_note",
    "update_note",
    "update_section",
    "map_overview",
}


def _payload(result) -> dict:
    """MCPServer tool results carry the dict as structured_content and/or a
    JSON string in the first text block — accept either. The field is
    snake_case since SDK 2.x; keep the camelCase read as a fallback so this
    script also runs against a v1-era server during a staged rollout."""
    structured = getattr(result, "structured_content", None)
    if structured is None:
        structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict) and structured:
        # MCPServer wraps plain-dict returns as {"result": ...} only for
        # non-object outputs; a dict comes through as-is.
        return structured.get("result", structured)
    return json.loads(result.content[0].text)


def _is_error(result) -> bool:
    """`isError` -> `is_error` in SDK 2.x; accept both."""
    flag = getattr(result, "is_error", None)
    if flag is None:
        flag = getattr(result, "isError", None)
    return bool(flag)


async def main() -> None:
    # SDK 2.x: headers/timeouts live on the httpx2 client instead of being
    # keywords on the transport helper. A bare AsyncClient would fall back to
    # httpx2's flat 5s timeout, too short for the long-lived GET stream.
    http_client = httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {TOKEN}"},
        timeout=httpx2.Timeout(30, read=300),
        follow_redirects=True,
    )
    try:
        await _smoke(http_client)
    finally:
        await http_client.aclose()


async def _smoke(http_client) -> None:
    # streamable_http_client yields a 2-tuple since SDK 2.x: the
    # get_session_id callback went away with the session concept that the
    # 2026-07-28 revision retired.
    async with streamable_http_client(URL, http_client=http_client) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            names = {t.name for t in tools.tools}
            missing = REQUIRED_TOOLS - names
            assert not missing, f"missing tools: {missing} (got {sorted(names)})"

            created = _payload(
                await session.call_tool(
                    "create_note",
                    {
                        "note_path": "01-NOTES/ci-smoke.md",
                        "content": "# CI smoke\n\nhello from ci\n",
                        "message": "ci: smoke create",
                    },
                )
            )
            assert created.get("action") == "created", created
            assert created.get("committed") is True, created

            note = _payload(
                await session.call_tool("read_note", {"note_ref": "01-NOTES/ci-smoke.md"})
            )
            assert "hello from ci" in note["content"], note
            content_hash = note["content_hash"]

            updated = _payload(
                await session.call_tool(
                    "update_note",
                    {
                        "note_ref": "01-NOTES/ci-smoke.md",
                        "content": "# CI smoke\n\nupdated by ci\n",
                        "expected_hash": content_hash,
                        "message": "ci: smoke update",
                    },
                )
            )
            assert updated.get("action") == "updated", updated
            assert updated.get("committed") is True, updated

            # Section-level CAS: edit one section, the sibling must survive
            # byte-identical and a stale section hash must be refused.
            created_sections = _payload(
                await session.call_tool(
                    "create_note",
                    {
                        "note_path": "01-NOTES/ci-smoke-sections.md",
                        "content": "# CI sections\n\nintro\n\n## Keep\n\nkeep body\n\n## Edit\n\nold body\n",
                        "message": "ci: smoke sections create",
                    },
                )
            )
            assert created_sections.get("committed") is True, created_sections

            section_note = _payload(
                await session.call_tool(
                    "read_note", {"note_ref": "01-NOTES/ci-smoke-sections.md"}
                )
            )
            by_heading = {s["heading"]: s for s in section_note.get("sections", [])}
            assert "## Edit" in by_heading and "## Keep" in by_heading, section_note

            section_updated = _payload(
                await session.call_tool(
                    "update_section",
                    {
                        "note_ref": "01-NOTES/ci-smoke-sections.md",
                        "section_heading": "## Edit",
                        "content": "## Edit\n\nnew body from ci\n",
                        "expected_hash": by_heading["## Edit"]["content_hash"],
                        "message": "ci: smoke section update",
                    },
                )
            )
            assert section_updated.get("action") == "updated_section", section_updated
            assert section_updated.get("committed") is True, section_updated

            after = _payload(
                await session.call_tool(
                    "read_note", {"note_ref": "01-NOTES/ci-smoke-sections.md"}
                )
            )
            assert "new body from ci" in after["content"], after
            assert "keep body" in after["content"], after

            stale = await session.call_tool(
                "update_section",
                {
                    "note_ref": "01-NOTES/ci-smoke-sections.md",
                    "section_heading": "## Edit",
                    "content": "## Edit\n\nmust not land\n",
                    "expected_hash": by_heading["## Edit"]["content_hash"],
                    "message": "ci: must be refused",
                },
            )
            stale_body = ""
            if stale.content:
                stale_body = getattr(stale.content[0], "text", "") or ""
            assert _is_error(stale) or "expected_hash" in stale_body, (
                f"stale section hash was not refused: is_error={_is_error(stale)} body={stale_body!r}"
            )

            # Write-time advisory: a dead wikilink is reported, never blocked.
            advisory = _payload(
                await session.call_tool(
                    "create_note",
                    {
                        "note_path": "01-NOTES/ci-smoke-advisory.md",
                        "content": "# Advisory\n\nvedi [[ci-ghost-target]]\n",
                        "message": "ci: smoke advisory",
                    },
                )
            )
            assert advisory.get("committed") is True, advisory
            unresolved = [e.get("target") for e in advisory.get("unresolved_links", [])]
            assert unresolved == ["ci-ghost-target"], advisory

            # Orientation tool: token-bounded structural overview.
            overview = _payload(await session.call_tool("map_overview", {}))
            assert "notes" in overview and "hubs" in overview, overview
            assert "broken_count" in overview and "orphans_count" in overview, overview

            # Guardrail: the write-exclusion for 99-SECRETS must hold.
            refused = await session.call_tool(
                "create_note",
                {
                    "note_path": "99-SECRETS/should-never-land.md",
                    "content": "nope",
                    "message": "ci: must be refused",
                },
            )
            body = ""
            if refused.content:
                body = getattr(refused.content[0], "text", "") or ""
            assert _is_error(refused) or "error" in body.lower() or "refus" in body.lower(), (
                f"write into 99-SECRETS was not refused: is_error={_is_error(refused)} body={body!r}"
            )

    print("vault-mcp smoke: OK")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except AssertionError as exc:
        print(f"vault-mcp smoke: FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
