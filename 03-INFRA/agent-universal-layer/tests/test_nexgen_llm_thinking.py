"""A local model without a thinking mode must not take the whole lane down with it.

The answer channel asked for thinking unconditionally. Ollama answers HTTP 400 "does not support thinking"
to that request on a model that has none (granite, qwen2.5-coder, plenty of 12B checkpoints), so setting
NEXGEN_LOCAL_MODEL to such a model made every question fail. Measured on a real Ollama, 2026-10-06.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("langchain_ollama")

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import httpx  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402
from nexgen_local import llm as llm_module  # noqa: E402
from nexgen_local.config import LaneConfig  # noqa: E402
from nexgen_local.llm import ChatOllamaLLM  # noqa: E402


class Reply:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("bad", request=None, response=None)

    def json(self):
        return self._payload


def test_capabilities_are_read_from_ollama(monkeypatch):
    calls = []
    monkeypatch.setattr(httpx, "post", lambda url, **kw: calls.append((url, kw)) or Reply({"capabilities": ["completion", "tools"]}))
    assert llm_module._ollama_capabilities("http://h:1/", "m") == frozenset({"completion", "tools"})
    assert calls[0][0] == "http://h:1/api/show" and calls[0][1]["json"] == {"model": "m"}


@pytest.mark.parametrize("failure", [httpx.ConnectError("down"), ValueError("not json")])
def test_unknown_capabilities_are_none_not_an_error(monkeypatch, failure):
    def post(url, **kw):
        raise failure
    monkeypatch.setattr(httpx, "post", post)
    assert llm_module._ollama_capabilities("http://h:1", "m") is None


def test_a_server_without_the_field_gives_none(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda url, **kw: Reply({"details": {}}))
    assert llm_module._ollama_capabilities("http://h:1", "m") is None


@pytest.mark.parametrize("capabilities,expected", [
    (frozenset({"completion", "thinking"}), True),
    (frozenset({"completion", "tools"}), None),
    (None, True),  # unknown: keep the old behaviour, the call-time fallback is the backstop
])
def test_thinking_is_asked_for_only_where_the_model_has_it(tmp_path, monkeypatch, capabilities, expected):
    monkeypatch.setattr(llm_module, "_ollama_capabilities", lambda host, tag, timeout=3.0: capabilities)
    model = ChatOllamaLLM(LaneConfig(vault_root=tmp_path, model="synthetic"))
    assert model._text_model.reasoning is expected
    assert model._json_model.reasoning is False
    assert model._decision_model.reasoning is False


def test_a_late_refusal_is_retried_once_without_thinking_and_remembered(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_module, "_ollama_capabilities", lambda host, tag, timeout=3.0: None)
    model = ChatOllamaLLM(LaneConfig(vault_root=tmp_path, model="synthetic"))

    class Refuses:
        async def ainvoke(self, messages):
            raise RuntimeError('"synthetic" does not support thinking (status code: 400)')

    class Answers:
        calls = 0

        async def ainvoke(self, messages):
            Answers.calls += 1
            return AIMessage(content="risposta")

    model._text_model = Refuses()
    import langchain_ollama

    monkeypatch.setattr(langchain_ollama, "ChatOllama", lambda **kw: Answers())
    assert model.text("sys", "user") == "risposta"
    assert isinstance(model._text_model, Answers)
    assert model.text("sys", "again") == "risposta"
    assert Answers.calls == 2


def test_any_other_failure_is_not_swallowed(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_module, "_ollama_capabilities", lambda host, tag, timeout=3.0: None)
    model = ChatOllamaLLM(LaneConfig(vault_root=tmp_path, model="synthetic"))

    class Broken:
        async def ainvoke(self, messages):
            raise RuntimeError("connection reset")

    model._text_model = Broken()
    with pytest.raises(llm_module.LLMError, match="connection reset"):
        model.text("sys", "user")
