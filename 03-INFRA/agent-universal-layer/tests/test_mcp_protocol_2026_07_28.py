"""Conformance tests for the MCP 2026-07-28 protocol revision.

Two bundled MCP servers had to move:

  - vault-ocr (deploy/ocr/mcp/vault_ocr_mcp.py) is hand-rolled JSON-RPC with
    no SDK, so the revision is implemented here by hand and is tested here by
    driving the real process over stdio. These are behavioural tests: they
    speak the wire protocol, they do not read the source.
  - vault-library (deploy/vault-mcp) delegates the protocol to the MCP Python
    SDK, so its conformance is the SDK's job once the code is on 2.x. What can
    regress silently is the *migration* — a revert to the v1 constructs still
    imports and still boots on SDK 1.x. The source guards at the bottom pin
    those constructs, because CI's engine-tests job installs pytest+pyyaml
    only and cannot import the SDK to check behaviour.

Nothing here touches the real OCR API or the tunnel: the one test that needs a
successful tool call points the server at a loopback stub instead.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import os
import subprocess
import sys
import threading

import pytest

REPO = Path(__file__).resolve().parents[3]
OCR_SERVER = REPO / "03-INFRA" / "deploy" / "ocr" / "mcp" / "vault_ocr_mcp.py"
VAULT_MCP = REPO / "03-INFRA" / "deploy" / "vault-mcp"

PROTOCOL_VERSION = "2026-07-28"
LEGACY_VERSION = "2025-06-18"

NEW_META = {
    "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
    "io.modelcontextprotocol/clientCapabilities": {},
    "io.modelcontextprotocol/clientInfo": {"name": "conformance", "version": "0"},
}


STUB_OCR_PAYLOAD = {
    "sha256": "0" * 64,
    "image": {"width": 12, "height": 8, "format": "PNG"},
    "lines": [{"text": "stubbed line", "confidence": 0.99}],
    "line_count": 1,
    "avg_confidence": 0.99,
    "engine": "stub",
    "elapsed_sec": 0.01,
    "markdown": "stubbed line",
}


class _StubOcrHandler(BaseHTTPRequestHandler):
    def _json(self, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler naming
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self._json(STUB_OCR_PAYLOAD)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler naming
        self._json({"status": "ok", "engine": "stub"})

    def log_message(self, *_args) -> None:
        pass


@pytest.fixture
def ocr_stub():
    """A loopback stand-in for the RapidOCR API, so a tool call can actually
    succeed without the tunnel. Yields the base URL."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubOcrHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def drive(requests: list[dict], extra_env: dict[str, str] | None = None) -> list[dict]:
    """Send requests to a fresh vault-ocr process over stdio, JSONL framing.

    Returns only the messages the server answered, in order. Notifications
    (no `id`) legitimately produce nothing.
    """
    payload = "".join(json.dumps(r) + "\n" for r in requests)
    proc = subprocess.run(
        [sys.executable, str(OCR_SERVER)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", **(extra_env or {})},
    )
    assert proc.returncode == 0, f"server exited {proc.returncode}: {proc.stderr}"
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


def one(request: dict, extra_env: dict[str, str] | None = None) -> dict:
    replies = drive([request], extra_env)
    assert len(replies) == 1, f"expected exactly one reply, got {replies}"
    return replies[0]


def call_extract(tmp_path: Path, stub_url: str) -> dict:
    """One genuinely successful ocr_extract_image call against the stub."""
    image = tmp_path / "shot.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n stub bytes")
    return one(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "ocr_extract_image",
                "arguments": {"image_path": str(image)},
                "_meta": NEW_META,
            },
        },
        {"VAULT_OCR_API_URL": stub_url},
    )


# --------------------------------------------------------------------------
# vault-ocr: the 2026-07-28 era
# --------------------------------------------------------------------------


def test_server_discover_is_implemented_and_advertises_the_new_revision():
    """`server/discover` is a MUST for servers on this revision."""
    reply = one({"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": {"_meta": NEW_META}})
    res = reply["result"]
    assert PROTOCOL_VERSION in res["supportedVersions"], res
    assert res["capabilities"]["tools"] == {"listChanged": False}, res
    assert res["instructions"], res


def test_tools_list_works_with_no_handshake_at_all():
    """The revision retired `initialize`: a client may open with tools/list."""
    reply = one({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": NEW_META}})
    names = {t["name"] for t in reply["result"]["tools"]}
    assert names == {"ocr_healthcheck", "ocr_extract_image", "ocr_extract_batch"}, names


def test_tools_call_works_with_no_handshake_at_all():
    """Same for tools/call — no method may be gated on initialize arriving.

    Uses an unknown tool name so the assertion is about dispatch, not about
    reaching the OCR API.
    """
    reply = one(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "does_not_exist", "arguments": {}, "_meta": NEW_META},
        }
    )
    assert "result" in reply, reply
    assert reply["result"]["isError"] is True, reply
    assert "unknown tool" in reply["result"]["content"][0]["text"], reply


@pytest.mark.parametrize(
    "method, params",
    [
        ("server/discover", {"_meta": NEW_META}),
        ("tools/list", {"_meta": NEW_META}),
        ("tools/call", {"name": "does_not_exist", "arguments": {}, "_meta": NEW_META}),
    ],
)
def test_every_result_carries_result_type_complete(method, params):
    """`resultType` is REQUIRED on every result at this revision."""
    reply = one({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    assert reply["result"]["resultType"] == "complete", reply


@pytest.mark.parametrize("method", ["server/discover", "tools/list"])
def test_cacheable_results_carry_ttl_and_scope(method):
    """`ttlMs` and `cacheScope` are required on the cacheable results."""
    reply = one({"jsonrpc": "2.0", "id": 1, "method": method, "params": {"_meta": NEW_META}})
    res = reply["result"]
    assert isinstance(res["ttlMs"], int) and res["ttlMs"] >= 0, res
    assert res["cacheScope"] in {"public", "private"}, res


@pytest.mark.parametrize("tool", ["does_not_exist", "ocr_extract_image"])
def test_tools_call_result_is_not_advertised_as_cacheable(tool, tmp_path, ocr_stub):
    """A CallToolResult is not a CacheableResult: inventing a TTL for it would
    invite a client to serve a stale OCR extraction from cache.

    Covers both dispatch paths — the rejected tool AND a real extraction that
    succeeded — because a cache hint leaked onto only the success path would
    otherwise go unnoticed.
    """
    if tool == "ocr_extract_image":
        reply = call_extract(tmp_path, ocr_stub)
    else:
        reply = one(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": tool, "arguments": {}, "_meta": NEW_META},
            }
        )
    assert "ttlMs" not in reply["result"], reply
    assert "cacheScope" not in reply["result"], reply


def test_a_successful_tool_call_carries_the_new_envelope(tmp_path, ocr_stub):
    """The happy path must be on the revision too, not just the error path."""
    reply = call_extract(tmp_path, ocr_stub)
    assert reply["result"]["resultType"] == "complete", reply
    assert reply["result"].get("isError") is not True, reply
    assert "stubbed line" in reply["result"]["content"][0]["text"], reply
    info = reply["result"]["_meta"]["io.modelcontextprotocol/serverInfo"]
    assert info["name"] == "vault-ocr", info


@pytest.mark.parametrize("method", ["server/discover", "tools/list"])
def test_results_identify_the_server_in_meta(method):
    """serverInfo moved out of the initialize result into every result's _meta."""
    reply = one({"jsonrpc": "2.0", "id": 1, "method": method, "params": {"_meta": NEW_META}})
    info = reply["result"]["_meta"]["io.modelcontextprotocol/serverInfo"]
    assert info["name"] == "vault-ocr", info
    assert info["version"], info


def test_unsupported_protocol_version_is_refused_with_the_documented_code():
    """-32022 with data.supported/data.requested, so the client can retry on a
    mutually supported version instead of guessing."""
    meta = {**NEW_META, "io.modelcontextprotocol/protocolVersion": "2099-01-01"}
    reply = one({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": meta}})
    err = reply["error"]
    assert err["code"] == -32022, reply
    assert err["data"]["requested"] == "2099-01-01", reply
    assert PROTOCOL_VERSION in err["data"]["supported"], reply


def test_tool_order_is_deterministic_across_calls():
    """The revision requires a stable tools/list ordering."""
    req = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": NEW_META}}
    first, second = drive([req, {**req, "id": 2}])
    assert [t["name"] for t in first["result"]["tools"]] == [
        t["name"] for t in second["result"]["tools"]
    ]


def test_unknown_method_still_answers_method_not_found():
    reply = one({"jsonrpc": "2.0", "id": 1, "method": "nope/nope", "params": {"_meta": NEW_META}})
    assert reply["error"]["code"] == -32601, reply


def test_a_notification_is_never_answered():
    """No `id` means no reply, at either revision."""
    assert drive([{"jsonrpc": "2.0", "method": "notifications/initialized"}]) == []


# --------------------------------------------------------------------------
# vault-ocr: the older era must keep working
# --------------------------------------------------------------------------


def test_legacy_initialize_handshake_still_works():
    """Every CLI in the council still opens with initialize; the migration is
    worthless if it locks them out."""
    replies = drive(
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": LEGACY_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "legacy", "version": "0"},
                },
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]
    )
    assert len(replies) == 2, replies
    init, listed = replies
    assert init["result"]["protocolVersion"] == LEGACY_VERSION, init
    assert init["result"]["serverInfo"]["name"] == "vault-ocr", init
    assert len(listed["result"]["tools"]) == 3, listed


def test_initialize_answers_a_supported_version_when_asked_for_an_unknown_one():
    reply = one(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "1999-01-01", "capabilities": {}},
        }
    )
    assert reply["result"]["protocolVersion"] == PROTOCOL_VERSION, reply


def test_a_request_with_no_meta_is_served_not_refused():
    """No `_meta` means an older client, not a broken one."""
    reply = one({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert "result" in reply, reply


def test_legacy_ping_is_answered():
    reply = one({"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert "result" in reply and "error" not in reply, reply


# --------------------------------------------------------------------------
# vault-library: SDK 2.x migration guards
# --------------------------------------------------------------------------


def _read(rel: str) -> str:
    return (VAULT_MCP / rel).read_text(encoding="utf-8")


def test_vault_mcp_pins_the_sdk_major_that_implements_the_revision():
    """SDK 1.x cannot serve 2026-07-28 at all, so the floor is the guard."""
    pyproject = _read("pyproject.toml")
    assert '"mcp>=2.0.0,<3.0.0"' in pyproject, pyproject


def test_vault_mcp_uses_the_sdk2_server_class():
    source = _read("src/vault_mcp_server/server.py")
    assert "from mcp.server.mcpserver import MCPServer" in source
    assert "FastMCP" not in source, "FastMCP is gone in SDK 2.x"


def test_vault_mcp_does_not_assign_the_removed_transport_setting():
    """`mcp.settings.streamable_http_path = ...` raises ValueError on SDK 2.x —
    the server would die at import, not misbehave subtly."""
    source = _read("src/vault_mcp_server/server.py")
    assert "settings.streamable_http_path =" not in source


@pytest.mark.parametrize(
    "keyword",
    ["streamable_http_path=", "stateless_http=", "json_response=", "transport_security="],
)
def test_transport_config_is_passed_where_the_asgi_app_is_built(keyword):
    """These moved off the constructor onto streamable_http_app(). Dropping one
    is silent: the SDK falls back to its own default (path /mcp, stateful, SSE
    responses, an auto host allowlist that rejects the real hostname)."""
    source = _read("src/vault_mcp_server/server.py")
    app_call = source.split("mcp.streamable_http_app(", 1)
    assert len(app_call) == 2, "streamable_http_app() is not called with arguments"
    built = app_call[1].split(")", 1)[0]
    assert keyword in built, f"{keyword} missing from streamable_http_app(...)"


def test_cors_allows_the_headers_the_revision_requires():
    """Mcp-Method and Mcp-Name are required on every Streamable HTTP POST; a
    browser origin cannot send them unless they are allowed."""
    source = _read("src/vault_mcp_server/server.py")
    allow = source.split("allow_headers=[", 1)[1].split("]", 1)[0]
    for header in ("Mcp-Method", "Mcp-Name", "MCP-Protocol-Version"):
        assert header in allow, f"{header} not in allow_headers"


def test_smoke_client_uses_the_sdk2_transport_helper():
    source = _read("ci-smoke.py")
    assert "streamable_http_client" in source
    assert "streamablehttp_client" not in source, "removed in SDK 2.x"
