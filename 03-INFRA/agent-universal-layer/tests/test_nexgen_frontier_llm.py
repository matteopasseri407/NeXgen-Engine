"""The lane over a frontier model: same contract as the local one, provider-neutral, with receipts."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("langchain_core")

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from langchain_core.messages import AIMessage  # noqa: E402
from nexgen_local import llm as llm_module  # noqa: E402
from nexgen_local.config import LaneConfig  # noqa: E402
from nexgen_local.llm import ChatModelLLM, LLMError, build_llm  # noqa: E402


class FakeChat:
    """A LangChain chat model reduced to what the adapter calls: ainvoke and with_structured_output."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        return self.replies.pop(0)

    def with_structured_output(self, schema, **kwargs):
        self.structured = (schema, kwargs)
        return self


def reply(text="", *, tokens=(10, 5), **metadata):
    return AIMessage(
        content=text,
        usage_metadata={"input_tokens": tokens[0], "output_tokens": tokens[1], "total_tokens": sum(tokens)},
        response_metadata=metadata,
    )


@pytest.fixture
def make(tmp_path):
    def build(*replies):
        fake = FakeChat(replies)
        cfg = LaneConfig(vault_root=tmp_path, model="synthetic")
        model = ChatModelLLM(cfg, "anthropic:claude-test", model_factory=lambda spec, **kw: fake)
        return model, fake
    return build


def test_json_reads_a_fenced_form(make):
    model, _ = make(reply('```json\n{"route": "mail"}\n```'))
    assert model.json("sys", "user") == {"route": "mail"}


@pytest.mark.parametrize("metadata", [{"stop_reason": "max_tokens"}, {"finish_reason": "length"}, {"done_reason": "length"}])
def test_truncation_is_recognised_whatever_the_provider_calls_it(make, metadata):
    model, _ = make(reply('{"route": "ma', **metadata))
    assert model.json("sys", "user") is None


def test_text_returns_the_answer_and_refuses_a_truncated_or_empty_one(make):
    model, _ = make(reply("Ecco."), reply("meta", stop_reason="max_tokens"), reply("   "))
    assert model.text("sys", "user") == "Ecco."
    with pytest.raises(LLMError):
        model.text("sys", "user")
    with pytest.raises(LLMError):
        model.text("sys", "user")


def test_choose_returns_an_action_from_the_menu_and_only_from_the_menu(make):
    parsed = {"action": "search", "arg": "invoice"}
    model, fake = make(
        {"raw": reply(""), "parsed": parsed},
        {"raw": reply(""), "parsed": {"action": "delete_everything"}},
        {"raw": reply("", stop_reason="max_tokens"), "parsed": parsed},
    )
    assert model.choose("sys", "user", ["search", "read"]) == parsed
    assert model.choose("sys", "user", ["search", "read"]) is None
    assert model.choose("sys", "user", ["search", "read"]) is None
    schema, kwargs = fake.structured
    assert schema["properties"]["action"]["enum"] == ["search", "read"]
    assert kwargs == {"include_raw": True}


def test_token_receipts_add_up_across_channels(make):
    model, _ = make(reply('{"a": 1}', tokens=(100, 20)), reply("answer", tokens=(300, 80)),
                    {"raw": reply("", tokens=(50, 5)), "parsed": {"action": "read"}})
    model.json("s", "u")
    model.text("s", "u")
    model.choose("s", "u", ["read"])
    assert model.usage == {"calls": 3, "input_tokens": 450, "output_tokens": 105, "total_tokens": 555}


def test_a_request_carrying_a_credential_never_leaves(make):
    import random
    import string

    rng = random.Random(3)
    secret = "sk-" + "ant-" + "".join(rng.choice(string.ascii_letters + string.digits) for _ in range(40))
    model, fake = make(reply("never read"))
    with pytest.raises(LLMError):
        model.text("sys", f"summarise this: API_KEY={secret}")
    assert fake.calls == []
    assert model.text("sys", "an ordinary request") == "never read"


@pytest.mark.parametrize("spec", ["claude", ":claude", "anthropic:"])
def test_a_spec_must_name_a_provider_and_a_model(tmp_path, spec):
    with pytest.raises(LLMError, match="provider"):
        ChatModelLLM(LaneConfig(vault_root=tmp_path, model="s"), spec, model_factory=lambda *a, **k: None)


def test_a_missing_langchain_is_a_typed_error_that_says_what_to_install(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "langchain", None)
    monkeypatch.setitem(sys.modules, "langchain.chat_models", None)
    with pytest.raises(LLMError, match="langchain"):
        ChatModelLLM(LaneConfig(vault_root=tmp_path, model="s"), "anthropic:x")


def test_the_environment_chooses_the_model(tmp_path, monkeypatch):
    cfg = LaneConfig(vault_root=tmp_path, model="s")
    local = object()
    monkeypatch.setattr(llm_module, "ChatOllamaLLM", lambda c: local)
    monkeypatch.delenv(llm_module.LANE_MODEL_ENV, raising=False)
    assert build_llm(cfg) is local
    fake = FakeChat([])
    monkeypatch.setattr(ChatModelLLM, "_init_chat_model", staticmethod(lambda spec, **kw: fake))
    monkeypatch.setenv(llm_module.LANE_MODEL_ENV, "openai:gpt-test")
    chosen = build_llm(cfg)
    assert isinstance(chosen, ChatModelLLM) and chosen.spec == "openai:gpt-test"
