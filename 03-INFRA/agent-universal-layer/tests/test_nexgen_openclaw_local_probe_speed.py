"""A local model catalogue is a set of waits, and waits can happen together.

`nexgen doctor --summary` reads the catalogue on every push. Each installed
Ollama model costs its own `/api/show` round trip and each backend its own
probe, so asking them in sequence made the sum of the waits the cost of one
line. They are asked together now; the catalogue they produce must be exactly
the same, in Ollama's own order.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.openclaw.detect import _detect_local  # noqa: E402


def _catalogue(url: str, timeout: float, data=None):
    if url.endswith("/api/tags"):
        return {"models": [{"name": f"model-{index}"} for index in range(6)]}
    if url.endswith("/api/show"):
        return {"model_info": {"llama.context_length": 4096}, "capabilities": ["completion"]}
    return None


def test_six_models_cost_one_wait_not_six():
    def slow_probe(url: str, timeout: float, data=None):
        time.sleep(0.15)
        return _catalogue(url, timeout, data)

    started = time.monotonic()
    found = _detect_local({}, slow_probe)
    spent = time.monotonic() - started

    assert [model.name for model in found.ollama] == [f"model-{index}" for index in range(6)]
    # Six sequential detail calls would be 6 * 0.15 = 0.9s on their own.
    assert spent < 0.7, spent


def test_the_catalogue_is_the_same_as_the_sequential_one():
    found = _detect_local({}, _catalogue)

    assert [model.name for model in found.ollama] == [f"model-{index}" for index in range(6)]
    assert all(model.context_window == 4096 for model in found.ollama)
    assert all(model.vision is False and model.reasoning is False for model in found.ollama)
    assert found.available is True


def test_a_wedged_backend_does_not_hold_the_other():
    def one_is_silent(url: str, timeout: float, data=None):
        if "/api/tags" in url:
            time.sleep(0.3)
            return {"models": [{"name": "slow-model"}]}
        return None

    started = time.monotonic()
    found = _detect_local({}, one_is_silent)
    spent = time.monotonic() - started

    assert [model.name for model in found.ollama] == ["slow-model"]
    assert found.llamacpp == ()
    assert spent < 0.6, spent
