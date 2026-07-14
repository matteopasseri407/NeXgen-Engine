"""CI smoke test for the bundled vault-library MCP server.

Run by .github/workflows/ci.yml (vault-mcp-smoke job) against a container
started from this directory's Dockerfile, mounted on a fixture vault +
bare repo. Exercises the real write path over streamable-http MCP:

    list tools -> create_note -> read_note -> update_note (hash guard)

plus one negative check (a write into 99-SECRETS must be refused). The
surrounding job verifies the produced Git commits afterwards.

Needs: pip install "mcp>=1.0,<2". Env: SMOKE_URL, SMOKE_TOKEN.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

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
}


def _payload(result) -> dict:
    """FastMCP tool results carry the dict as structuredContent and/or a
    JSON string in the first text block — accept either."""
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict) and structured:
        # FastMCP wraps plain-dict returns as {"result": ...} only for
        # non-object outputs; a dict comes through as-is.
        return structured.get("result", structured)
    return json.loads(result.content[0].text)


async def main() -> None:
    headers = {"Authorization": f"Bearer {TOKEN}"}
    async with streamablehttp_client(URL, headers=headers) as (read, write, _):
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
            assert refused.isError or "error" in body.lower() or "refus" in body.lower(), (
                f"write into 99-SECRETS was not refused: isError={refused.isError} body={body!r}"
            )

    print("vault-mcp smoke: OK")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except AssertionError as exc:
        print(f"vault-mcp smoke: FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
