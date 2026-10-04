"""Use the actual LangChain/Ollama adapter against an offline HTTP endpoint."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from nexgen_local.config import LaneConfig
from nexgen_local.llm import ChatOllamaLLM, LLMError


@pytest.fixture
def endpoint(monkeypatch):
    pytest.importorskip("langchain_ollama")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            if self.server.mode == "stall":
                time.sleep(0.6)
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()
            chunks = 30 if self.server.mode == "stream" else 1
            try:
                for i in range(chunks):
                    value = {
                        "model": "synthetic",
                        "done": i == chunks - 1,
                        "message": {"role": "assistant", "content": '{"action":"answer","arg":"","why":"ok"}'},
                    }
                    if value["done"]:
                        value["done_reason"] = getattr(self.server, "finish", "stop")
                    self.wfile.write(json.dumps(value).encode() + b"\n")
                    self.wfile.flush()
                    if chunks > 1:
                        time.sleep(0.03)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server.mode = "stall"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("OLLAMA_HOST", f"http://127.0.0.1:{server.server_port}")
    import nexgen_local.llm as module

    for channel in ("JSON", "TEXT", "DECISION"):
        monkeypatch.setattr(module, channel + "_TIMEOUT_SECONDS", 0.15)
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


@pytest.mark.parametrize("channel", ["json", "text", "choose"])
@pytest.mark.parametrize("mode", ["stall", "stream"])
def test_deadline_interrupts_stall_and_continuous_stream(endpoint, tmp_path, channel, mode):
    endpoint.mode = mode
    llm = ChatOllamaLLM(LaneConfig(vault_root=tmp_path, model="synthetic"))
    started = time.monotonic()
    try:
        with pytest.raises(LLMError):
            if channel == "choose":
                llm.choose("synthetic", "synthetic", ["answer"])
            else:
                getattr(llm, channel)("synthetic", "synthetic")
        assert time.monotonic() - started < 0.6
    finally:
        if hasattr(llm, "close"):
            llm.close()


def test_adapter_reuses_connection_after_cancellation(endpoint, tmp_path):
    llm = ChatOllamaLLM(LaneConfig(vault_root=tmp_path, model="synthetic"))
    try:
        with pytest.raises(LLMError):
            llm.json("synthetic", "synthetic")
        endpoint.mode = "ok"
        assert llm.json("synthetic", "synthetic")["action"] == "answer"
        assert llm.json("synthetic", "synthetic")["action"] == "answer"
    finally:
        if hasattr(llm, "close"):
            llm.close()


def test_fallback_rejects_truncation_and_operational_errors():
    class Backend:
        async def ainvoke(self, *_a, **_k):
            return self.invoke()

        def invoke(self, *_a, **_k):
            return SimpleNamespace(content='{"action":"answer"}', response_metadata={"done_reason": "length"})

        def bind(self, **kwargs):
            assert kwargs == {"format": "json"}
            return self

        def with_structured_output(self, *_a, **_k):
            raise NotImplementedError("schema unsupported")

    backend = Backend()
    llm = ChatOllamaLLM.__new__(ChatOllamaLLM)
    llm._json_model = None  # fallback must retain the decision model, not the router
    llm._decision_model = backend
    try:
        assert llm.choose("synthetic", "synthetic", ["answer"]) is None

        def fail(*_a, **_k):
            raise ValueError("invalid configuration")

        backend.with_structured_output = fail
        with pytest.raises(LLMError, match="invalid configuration"):
            llm.choose("synthetic", "synthetic", ["answer"])
    finally:
        if hasattr(llm, "close"):
            llm.close()


def test_structured_decision_rejects_valid_json_truncated_by_budget(endpoint, tmp_path):
    endpoint.mode = "ok"
    endpoint.finish = "length"
    llm = ChatOllamaLLM(LaneConfig(vault_root=tmp_path, model="synthetic"))
    try:
        assert llm.choose("synthetic", "synthetic", ["answer"]) is None
    finally:
        llm.close()
