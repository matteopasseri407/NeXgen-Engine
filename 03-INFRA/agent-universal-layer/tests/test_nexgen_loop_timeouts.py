"""A model that stalls costs one deadline, not two.

The decision step swallowed every error from the model and asked again, a timeout included, so a stalled
local model held the loop for twice its deadline before the lane gave up. A malformed answer still earns
its one repair.
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_local import llm as llm_module  # noqa: E402
from nexgen_local import steps  # noqa: E402
from nexgen_local.llm import LLMError, LLMTimeout  # noqa: E402


class Counting:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def choose(self, system, user, actions):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _state():
    from nexgen_local.step_state import LoopState

    return LoopState(task="cerca la nota") if "task" in LoopState.__dataclass_fields__ else None


@pytest.fixture
def ask(monkeypatch):
    monkeypatch.setattr(steps, "decision_prompt", lambda state, menu: "menu")
    return lambda llm: steps._ask(llm, None, [], ["answer", "escalate"])


def test_a_timeout_is_not_asked_again(ask):
    model = Counting(LLMTimeout("scaduto"), {"action": "answer"})
    assert ask(model) is None
    assert model.calls == 1


def test_a_malformed_answer_still_gets_one_repair(ask):
    model = Counting({"action": "something-else"}, {"action": "answer"})
    assert ask(model) == {"action": "answer"}
    assert model.calls == 2


def test_a_parser_error_still_gets_one_repair(ask):
    model = Counting(ValueError("not json"), {"action": "escalate"})
    assert ask(model) == {"action": "escalate"}
    assert model.calls == 2


@pytest.mark.parametrize("exc,kind", [
    (TimeoutError("late"), LLMTimeout),
    (httpx.ReadTimeout("slow"), LLMTimeout),
    (httpx.ConnectTimeout("slow"), LLMTimeout),
    (httpx.ConnectError("down"), LLMError),
    (RuntimeError("boom"), LLMError),
])
def test_failures_are_classified_the_same_way_on_every_channel(exc, kind):
    failure = llm_module._failure(exc, "chiamata")
    assert type(failure) is kind


def test_the_real_deadline_path_produces_a_timeout(tmp_path):
    """A model that never answers, through the adapter's own deadline and its wrappers."""
    import asyncio

    from nexgen_local.config import LaneConfig

    class Never:
        async def ainvoke(self, messages):
            await asyncio.sleep(30)

    monkeypatched = {"_TEXT": 0.2}
    model = llm_module.ChatModelLLM(LaneConfig(vault_root=tmp_path, model="s"), "anthropic:x",
                                    model_factory=lambda spec, **kw: Never())
    original = llm_module.TEXT_TIMEOUT_SECONDS
    llm_module.TEXT_TIMEOUT_SECONDS = monkeypatched["_TEXT"]
    try:
        with pytest.raises(LLMTimeout):
            model.text("sys", "user")
    finally:
        llm_module.TEXT_TIMEOUT_SECONDS = original
