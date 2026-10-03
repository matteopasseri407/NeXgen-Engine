"""Model access: a tiny interface, one LangChain implementation.

The lane only ever needs three verbs from a model: fill a JSON form (routing),
answer in text from provided content, and choose the next action from a closed
menu (the bounded loop). Keeping the interface this small is what makes the
framework replaceable: the graph, the loop and the engine talk to ``LLM``, not
to LangChain.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
import weakref
from typing import Protocol

from nexgen_core.errors import NexgenError

from .config import LaneConfig


class LLMError(NexgenError, RuntimeError):
    """The model could not be reached or its driver is missing."""


class LLM(Protocol):
    """What the lane needs from a model. Tests implement this with a fake."""

    def json(self, system: str, user: str) -> dict | None: ...

    def text(self, system: str, user: str) -> str: ...

    def choose(self, system: str, user: str, actions: list[str]) -> dict | None: ...


def _json_block(text: str) -> dict | None:
    clean = re.sub(r"^```(?:json)?|```$", "", str(text).strip(), flags=re.M).strip()
    try:
        parsed = json.loads(clean)
    except (TypeError, ValueError):
        match = re.search(r"\{.*\}", clean, flags=re.S)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except ValueError:
            return None
    return parsed if isinstance(parsed, dict) else None


#: The router sees a short prompt and answers with a small JSON form: a small
#: context keeps the 4B-class router light while the answerer keeps cfg.num_ctx.
ROUTER_NUM_CTX = 4096

#: Decision cap: the menu prompt is short, but a thinking trace before the
#: choice can run long. Context is cheap on this box (measured: 8K -> 82K
#: moves residency by ~0.1GB), thinking tokens are not.
DECISION_NUM_CTX = 16384

#: Wall-clock bounds per channel (seconds). num_predict caps how much the
#: model may generate, not how long a stalled connection may hang: a
#: runaway once pinned the GPU for 17+ minutes with a dead client. These
#: Cancel the native async request at the deadline, even when the provider
#: continues streaming. HTTP inactivity timeouts alone cannot do that.
JSON_TIMEOUT_SECONDS = 180
TEXT_TIMEOUT_SECONDS = 600
DECISION_TIMEOUT_SECONDS = 180


def _close_runtime(runner, transport=None) -> None:
    try:
        if transport is not None:
            runner.run(transport.aclose())
    finally:
        runner.close()


class ChatOllamaLLM:
    """Synchronous contract backed by cancellable LangChain/Ollama requests."""

    def __init__(self, cfg: LaneConfig) -> None:
        try:
            from langchain_ollama import ChatOllama
            from httpx import AsyncHTTPTransport
        except ImportError as exc:  # pragma: no cover - exercised on machines without the extra
            raise LLMError(
                "driver mancante: reinstalla nexgen-engine"
            ) from exc
        host = os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434"
        if "://" not in host:
            host = "http://" + host
        self._runner = asyncio.Runner()
        self._invoke_lock = threading.Lock()
        transport = AsyncHTTPTransport()
        # A single owned transport and event loop survive consecutive calls.
        # Cleanup uses HTTPX's public transport API, not driver internals.
        self._finalizer = weakref.finalize(self, _close_runtime, self._runner, transport)
        clients = {"async_client_kwargs": {"transport": transport}}
        router_common = {
            "model": cfg.router_tag,
            "base_url": host,
            "temperature": 0.0,
            "num_ctx": min(cfg.num_ctx, ROUTER_NUM_CTX),
            # A 4-field form; cap it like decisions.
            "num_predict": 512,
            "validate_model_on_init": False,
            "client_kwargs": {"timeout": JSON_TIMEOUT_SECONDS},
            **clients,
        }
        answer_common = {
            "model": cfg.answer_tag,
            "base_url": host,
            "temperature": cfg.temperature,
            "num_ctx": cfg.num_ctx,
            # Bound: thinking + prose share this budget. A runaway thought
            # once pinned the GPU for 17+ minutes with a dead client;
            # 2048 tokens (~6-8K chars) cover every draft and answer the
            # lane allows (bodies cap at 20K chars, answers stay concise).
            "num_predict": 2048,
            "validate_model_on_init": False,
            "client_kwargs": {"timeout": TEXT_TIMEOUT_SECONDS},
            **clients,
        }
        # The loop's decision channel: the answerer's competence, the router's
        # small context. One decision is a short forced-schema object.
        decision_common = {
            "model": cfg.answer_tag,
            "base_url": host,
            "temperature": 0.0,
            "num_ctx": min(cfg.num_ctx, DECISION_NUM_CTX),
            # A decision is ~50 tokens of JSON; never let a ramble hang
            # the loop.
            "num_predict": 256,
            "validate_model_on_init": False,
            "client_kwargs": {"timeout": DECISION_TIMEOUT_SECONDS},
            **clients,
        }
        try:
            self._json_model = ChatOllama(format="json", reasoning=False, **router_common)
            # Prosa con pensiero (bozze, risposte): niente schema forzato qui,
            # il CoT aiuta e non rompe nulla. Misurato sul golden set.
            self._text_model = ChatOllama(reasoning=True, **answer_common)
            # Decisioni SENZA pensiero: il canale e' uno schema forzato
            # (json_schema) e il think ci va a cazzotti — misurato: 12/16
            # e p95 decisione da 5s a 170s. Scelta secca, niente rimuginii.
            self._decision_model = ChatOllama(reasoning=False, **decision_common)
        except TypeError:  # older driver without reasoning/validate switches
            self._json_model = ChatOllama(format="json", **router_common)
            self._text_model = ChatOllama(**answer_common)
            self._decision_model = ChatOllama(**decision_common)

    def close(self) -> None:
        """Release this adapter's connections and loop; safe to call twice."""
        if hasattr(self, "_finalizer"):
            with self._invoke_lock:
                self._finalizer()
        elif hasattr(self, "_runner"):
            self._runner.close()

    def _invoke(self, model, messages, timeout: float):
        """The synchronous LLM contract drives a cancellable async request."""
        # Also supports injected backends without constructing a real client.
        if not hasattr(self, "_runner"):
            self._runner = asyncio.Runner()
            self._invoke_lock = threading.Lock()
        deadline = time.monotonic() + timeout
        if not self._invoke_lock.acquire(timeout=max(0, timeout)):
            raise TimeoutError("model deadline expired while waiting for another call")
        try:
            async def call():
                async with asyncio.timeout(max(0, deadline - time.monotonic())):
                    return await model.ainvoke(messages)
            try:
                return self._runner.run(call())
            except TimeoutError as exc:
                raise TimeoutError(f"tempo massimo del modello superato ({timeout:g}s)") from exc
        finally:
            self._invoke_lock.release()

    @staticmethod
    def _messages(system: str, user: str):
        from langchain_core.messages import HumanMessage, SystemMessage

        return [SystemMessage(content=system), HumanMessage(content=user)]

    @staticmethod
    def _content(message) -> str:
        content = getattr(message, "content", "")
        if isinstance(content, str):
            return content
        return json.dumps(content, ensure_ascii=False)

    @staticmethod
    def _done_reason(message) -> str:
        """How generation ended: "stop", "length" (budget exhausted), ..."""
        meta = getattr(message, "response_metadata", {}) or {}
        return str(meta.get("done_reason") or "stop")

    @classmethod
    def _checked_text(cls, message, *, what: str) -> str:
        """Prose out of a generation, fail-closed on truncation or void.

        The output bound (num_predict) covers thinking AND response: a
        budget eaten by the think channel returns done_reason="length"
        with empty content. Returning that as answer="" would be a silent
        success with exit 0, so truncation and empty both raise into the
        lane's typed error channel instead.
        """
        text = cls._content(message)
        if cls._done_reason(message) == "length":
            raise LLMError(f"{what} troncata dal budget di generazione (done_reason=length)")
        if not text.strip():
            raise LLMError(f"{what} vuota dal modello")
        return text

    def json(self, system: str, user: str) -> dict | None:
        try:
            message = self._invoke(self._json_model, self._messages(system, user), JSON_TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001 - surface a typed error upward
            raise LLMError(f"chiamata al modello fallita: {exc}") from exc
        if self._done_reason(message) == "length":
            # Truncated form, same as unparseable: every caller degrades
            # gracefully (fallback route, refused proposal), the router
            # never aborts the lane.
            return None
        return _json_block(self._content(message))

    def text(self, system: str, user: str) -> str:
        try:
            message = self._invoke(self._text_model, self._messages(system, user), TEXT_TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"chiamata al modello fallita: {exc}") from exc
        return self._checked_text(message, what="risposta del modello")

    def choose(self, system: str, user: str, actions: list[str]) -> dict | None:
        """Forced-schema decision for the bounded loop: one action from the menu."""
        schema = {
            "title": "LaneDecision",
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(actions)},
                "arg": {"type": "string", "description": "query o percorso, vuoto se non serve"},
                "why": {"type": "string", "description": "una riga"},
            },
            "required": ["action"],
        }
        deadline = time.monotonic() + DECISION_TIMEOUT_SECONDS
        try:
            structured = self._decision_model.with_structured_output(schema, method="json_schema", include_raw=True)
            result = self._invoke(structured, self._messages(system, user), DECISION_TIMEOUT_SECONDS)
        except Exception as exc:
            # Old driver without json_schema support: degrade to the plain
            # JSON channel and validate the menu locally, instead of failing
            # every decision into escalation. A timeout is not a missing
            # feature — it surfaces as a typed error, never a silent retry
            # that would double the hang.
            if not isinstance(exc, NotImplementedError):
                raise LLMError(f"decisione del modello fallita: {exc}") from exc
            try:
                fallback = self._invoke(self._decision_model.bind(format="json"), self._messages(system, user), deadline - time.monotonic())
            except Exception as fallback_exc:  # noqa: BLE001
                raise LLMError(f"decisione del modello fallita: {fallback_exc}") from fallback_exc
            if self._done_reason(fallback) == "length":
                return None
            parsed = _json_block(self._content(fallback))
            if isinstance(parsed, dict) and parsed.get("action") in actions:
                return parsed
            return None
        if isinstance(result, dict) and "raw" in result:
            if self._done_reason(result["raw"]) == "length":
                return None
            result = result.get("parsed")
        if not isinstance(result, dict):
            dump = getattr(result, "model_dump", None)
            result = dump() if callable(dump) else None
        return result if isinstance(result, dict) and result.get("action") in actions else None
