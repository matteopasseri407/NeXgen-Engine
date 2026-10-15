"""Pinning dello split llm: fissa il comportamento attuale, non quello desiderato.

L'estrazione delle tre factory (_build_ollama / _build_frontier / _build_thinking)
non deve cambiare esiti. Solo client finti, solo tmp_path, nessuna rete,
nessuna chiave vera (segreti solo sintetici), timeout corti.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

pytest.importorskip("langchain_core")
pytest.importorskip("langchain_ollama")

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from langchain_core.messages import AIMessage  # noqa: E402

from nexgen_local import llm as llm_module  # noqa: E402
from nexgen_local.config import LaneConfig  # noqa: E402
from nexgen_local.llm import ChatModelLLM, ChatOllamaLLM, LLMError, build_llm  # noqa: E402
from nexgen_local.tiers import get_tier  # noqa: E402


class FakeChat:
    """Chat finta: solo ainvoke + with_structured_output, nessuna rete."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        return self.replies.pop(0)

    def with_structured_output(self, schema, **kwargs):
        self.structured = (schema, kwargs)
        return self


class FakeOllama:
    """ChatOllama finto: registra kwargs, nessuna rete."""

    instances: list = []

    def __init__(self, **kwargs):
        self.kwargs = dict(kwargs)
        self.reasoning = kwargs.get("reasoning", "missing")
        FakeOllama.instances.append(self)

    def with_structured_output(self, schema, **kwargs):
        self.structured = (schema, kwargs)
        return FakeChat([{"raw": _msg(""), "parsed": {"action": "answer"}}])

    def bind(self, **kwargs):
        self.bound = dict(kwargs)
        return self

    async def ainvoke(self, messages):
        return _msg('{"action":"answer"}')


def _msg(text="", **meta):
    return AIMessage(
        content=text,
        usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        response_metadata=meta,
    )


@pytest.fixture
def short_timeouts(monkeypatch):
    monkeypatch.setattr(llm_module, "JSON_TIMEOUT_SECONDS", 2.0)
    monkeypatch.setattr(llm_module, "TEXT_TIMEOUT_SECONDS", 2.0)
    monkeypatch.setattr(llm_module, "DECISION_TIMEOUT_SECONDS", 2.0)


@pytest.fixture
def fake_ollama(monkeypatch):
    FakeOllama.instances.clear()
    monkeypatch.setattr(llm_module, "_ollama_capabilities", lambda host, tag, timeout=3.0: frozenset({"completion", "thinking"}))
    import langchain_ollama

    monkeypatch.setattr(langchain_ollama, "ChatOllama", FakeOllama)
    return FakeOllama


def _cfg(tmp_path, tier="tutor"):
    return LaneConfig(vault_root=tmp_path, model="synthetic", tier=tier)


@pytest.mark.parametrize("tier", ["tutor", "guided", "assisted"])
def test_choose_returns_menu_action_for_known_tiers(tmp_path, short_timeouts, tier):
    parsed = {"action": "read", "arg": "", "why": "ok"}
    fake = FakeChat([{"raw": _msg(""), "parsed": parsed}])
    model = ChatModelLLM(_cfg(tmp_path, tier), "anthropic:claude-test", model_factory=lambda spec, **kw: fake)
    try:
        assert model.choose("sys", "user", ["read", "answer"]) == parsed
        schema, _ = fake.structured
        assert schema["properties"]["action"]["enum"] == ["read", "answer"]
    finally:
        model.close()


def test_build_llm_chooses_right_family(tmp_path, monkeypatch, fake_ollama):
    cfg = _cfg(tmp_path)
    monkeypatch.delenv(llm_module.LANE_MODEL_ENV, raising=False)
    local = build_llm(cfg)
    try:
        assert isinstance(local, ChatOllamaLLM)
    finally:
        local.close()
    fake = FakeChat([])
    monkeypatch.setattr(ChatModelLLM, "_init_chat_model", staticmethod(lambda spec, **kw: fake))
    monkeypatch.setenv(llm_module.LANE_MODEL_ENV, "anthropic:claude-test")
    chosen = build_llm(cfg)
    try:
        assert isinstance(chosen, ChatModelLLM)
        assert chosen.spec == "anthropic:claude-test"
    finally:
        chosen.close()


@pytest.mark.parametrize("tier", ["tutor", "guided", "assisted"])
def test_ollama_adapter_has_three_channels_for_known_tiers(tmp_path, tier, fake_ollama):
    model = ChatOllamaLLM(_cfg(tmp_path, tier))
    try:
        assert isinstance(model, ChatOllamaLLM)
        assert model._json_model is not None and model._text_model is not None and model._decision_model is not None
        structured = model._structured_decision({"title": "T", "type": "object", "properties": {}})
        _, kwargs = model._decision_model.structured
        assert kwargs.get("method") == "json_schema"
        assert structured is not None
    finally:
        model.close()


def test_unknown_tier_raises_clear_error(tmp_path):
    with pytest.raises(ValueError, match="tier sconosciuto"):
        LaneConfig(vault_root=tmp_path, model="s", tier="sconosciuto")
    with pytest.raises(ValueError, match="tier sconosciuto"):
        get_tier("sconosciuto")
    from types import SimpleNamespace

    bad = SimpleNamespace(tier="sconosciuto", router_tag="m", answer_tag="m", num_ctx=4096, temperature=0.0)
    with pytest.raises(ValueError, match="tier sconosciuto"):
        ChatOllamaLLM(bad)


def test_no_secret_in_built_objects(tmp_path, short_timeouts, fake_ollama):
    from nexgen_core import secret_shapes

    # Sintetici, mai chiavi vere.
    fake_token = "sk-ant-" + "A" * 30
    pem = "-----BEGIN PRIVATE KEY-----\n" + "B" * 40 + "\n-----END PRIVATE KEY-----"
    assert secret_shapes.PROVIDER_TOKEN.search(fake_token)
    assert secret_shapes.PEM_BLOCK.search(pem)

    built = []

    def factory(spec, **kwargs):
        built.append(kwargs)
        return FakeChat([_msg("ok")])

    model = ChatModelLLM(_cfg(tmp_path), "anthropic:claude-test", model_factory=factory)
    try:
        dump = repr(built) + repr(model.__dict__)
        assert fake_token not in dump and pem not in dump
        assert not secret_shapes.PROVIDER_TOKEN.search(dump)
        assert not secret_shapes.PEM_BLOCK.search(dump)
        with pytest.raises(LLMError):
            model.text("sys", f"riassumi API_KEY={fake_token}")
        with pytest.raises(LLMError):
            model.text("sys", pem)
    finally:
        model.close()

    ollama = ChatOllamaLLM(_cfg(tmp_path))
    try:
        dump = repr([m.kwargs for m in FakeOllama.instances]) + repr(ollama.__dict__)
        assert not secret_shapes.PROVIDER_TOKEN.search(dump)
        assert not secret_shapes.PEM_BLOCK.search(dump)
    finally:
        ollama.close()


def test_public_signatures_unchanged():
    assert list(inspect.signature(ChatOllamaLLM.__init__).parameters) == ["self", "cfg"]
    params = inspect.signature(ChatModelLLM.__init__).parameters
    assert list(params) == ["self", "cfg", "spec", "model_factory"]
    assert params["model_factory"].default is None
    assert list(inspect.signature(build_llm).parameters) == ["cfg"]
    assert list(inspect.signature(get_tier).parameters) == ["name"]
