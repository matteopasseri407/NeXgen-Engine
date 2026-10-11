"""Rizzo PII bridge: protocol shape and VPS-first/local-fallback order, no HTTP."""
import importlib.util
import io
import json
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path


def load_bridge(monkeypatch=None):
    target = Path(__file__).resolve().parents[2] / "deploy/pii/mcp/rizzo_pii_mcp.py"
    spec = importlib.util.spec_from_file_location("pii_test", target)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def call(module, payload):
    buf = io.StringIO()
    with redirect_stdout(buf):
        module.handle(payload)
    return json.loads(buf.getvalue().strip().splitlines()[-1])


def test_tools_list_names_pii_anonymize():
    module = load_bridge()
    out = call(module, {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    assert [t["name"] for t in out["result"]["tools"]] == ["pii_anonymize"]


def test_unknown_tool_and_missing_text_are_errors():
    module = load_bridge()
    out = call(module, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": "nope", "arguments": {}}})
    assert out["error"]["code"] == -32601
    out = call(module, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                        "params": {"name": "pii_anonymize", "arguments": {"text": "  "}}})
    assert out["error"]["code"] == -32602


def test_primary_first_then_fallback(monkeypatch):
    module = load_bridge()
    seen = []

    def fake_post(url, payload, timeout):
        seen.append(url)
        if len(seen) == 1:
            raise urllib.error.URLError("vps giu")
        assert payload == {"text": "Mario Rossi", "include_mapping": False}
        return {"anonymized_text": "[FULLNAME_1]", "by_label": {"FULLNAME": 1}}

    monkeypatch.setattr(module, "_post", fake_post)
    monkeypatch.setattr(module, "_endpoint", lambda: "http://fallback")
    data, via = module.anonymize("Mario Rossi", mapping=False)
    assert via == "locale"
    assert seen[0].endswith(":33004") or "33004" in seen[0]
    assert seen[1] == "http://fallback"
    assert data["anonymized_text"] == "[FULLNAME_1]"


def test_both_down_reports_unreachable(monkeypatch):
    module = load_bridge()

    def boom(url, payload, timeout):
        raise urllib.error.URLError("tutto giu")

    monkeypatch.setattr(module, "_post", boom)
    monkeypatch.setattr(module, "_endpoint", lambda: "http://fallback")
    out = call(module, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": "pii_anonymize",
                                   "arguments": {"text": "Mario Rossi"}}})
    assert out["error"]["code"] == -32000


def test_bridge_imports_stdlib_only():
    src = (Path(__file__).resolve().parents[2] / "deploy/pii/mcp/rizzo_pii_mcp.py").read_text(encoding="utf-8")
    third_party = [ln for ln in src.splitlines()
                   if ln.startswith(("import ", "from "))
                   and not any(ln.startswith(p) for p in
                               ("import json", "import os", "import subprocess", "import sys",
                                "import time", "import urllib", "from "))]
    assert third_party == []
