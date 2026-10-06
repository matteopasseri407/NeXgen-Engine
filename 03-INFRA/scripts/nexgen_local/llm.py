"""Model access: a tiny interface, one LangChain implementation.

The lane only ever needs three verbs from a model: fill a JSON form (routing),
answer in text from provided content, and choose the next action from a closed
menu (the bounded loop). Keeping the interface this small is what makes the
framework replaceable: the graph, the loop and the engine talk to ``LLM``, not
to LangChain.
"""
from __future__ import annotations

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


class LLMTimeout(LLMError):
    """The model did not answer within its deadline.

    Kept apart from a bad answer on purpose: a malformed reply is worth one repair, a stalled model is
    not worth a second wait of the same length.
    """


def _is_timeout(exc: BaseException) -> bool:
    """A deadline of ours (asyncio) or of the HTTP client (httpx names its own, not the builtin)."""
    if isinstance(exc, TimeoutError):
        return True
    import httpx

    return isinstance(exc, httpx.TimeoutException)


def _failure(exc: Exception, what: str) -> LLMError:
    """The typed error for a failed call: a timeout is told apart from every other failure."""
    return (LLMTimeout if _is_timeout(exc) else LLMError)(f"{what}: {exc}")


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


def _ollama_capabilities(host: str, tag: str, timeout: float = 3.0) -> frozenset[str] | None:
    """What the model at `tag` can do, as Ollama reports it (`completion`, `tools`, `thinking`, ...).

    None when it cannot be learned (an older Ollama without the field, the server down): the caller then
    keeps its default and the call-time fallback in `text()` covers the rest.
    """
    import httpx

    try:
        reply = httpx.post(f"{host.rstrip('/')}/api/show", json={"model": tag}, timeout=timeout)
        reply.raise_for_status()
        caps = reply.json().get("capabilities")
    except (httpx.HTTPError, ValueError, OSError):
        return None
    return frozenset(str(c) for c in caps) if isinstance(caps, list) else None


def _close_runtime(runner, transport=None) -> None:
    try:
        if transport is not None:
            runner.run(transport.aclose())
    finally:
        runner.close()


class _ChatModelLLM:
    """The lane's three verbs over LangChain chat models, whatever backs them.

    A subclass builds `_json_model` (routing forms), `_text_model` (answers) and `_decision_model`
    (the bounded loop's closed menu). Everything else, the deadlines that cancel a stalled request,
    the fail-closed handling of truncated or empty output, the token receipts, lives here once.
    """

    def _check_outbound(self, messages) -> None:
        """Hook: refuse a request before it leaves the machine. A local model has nothing to refuse."""

    def _structured_decision(self, schema: dict):
        """The decision model constrained to `schema`, raw reply kept so truncation is visible."""
        return self._decision_model.with_structured_output(schema, include_raw=True)

    def _plain_json_decision(self):
        """The decision model asked for plain JSON, for a driver without structured output."""
        return self._decision_model

    def close(self) -> None:
        """Release this adapter's connections and loop; safe to call twice."""
        if hasattr(self, "_finalizer"):
            with self._invoke_lock:
                self._finalizer()
        elif hasattr(self, "_runner"):
            self._runner.close()

    def _invoke(self, model, messages, timeout: float):
        """The synchronous LLM contract drives a cancellable async request."""
        import asyncio

        self._check_outbound(messages)

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
                result = self._runner.run(call())
            except TimeoutError as exc:
                raise TimeoutError(f"tempo massimo del modello superato ({timeout:g}s)") from exc
            self._record_usage(result)
            return result
        finally:
            self._invoke_lock.release()

    def _record_usage(self, result) -> None:
        """Adds the token counts a reply carries (`usage_metadata`, provider-neutral) to the receipts.

        A frontier model is billed per token and a local one is not, but both report them the same
        way through LangChain, so the lane can say what a run cost without knowing the provider.
        """
        message = result.get("raw") if isinstance(result, dict) and "raw" in result else result
        meta = getattr(message, "usage_metadata", None)
        if not isinstance(meta, dict):
            return
        totals = self.__dict__.setdefault("_usage", {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
        totals["calls"] += 1
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            value = meta.get(key)
            if isinstance(value, int):
                totals[key] += value

    @property
    def usage(self) -> dict[str, int]:
        """Token receipts so far: calls, input, output, total."""
        return dict(self.__dict__.get("_usage") or {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0})

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
        """How generation ended: "stop", "length" (budget exhausted), ...

        Each provider names it differently (Ollama `done_reason`, Anthropic `stop_reason`, OpenAI
        `finish_reason`) and spells a spent budget differently (`length`, `max_tokens`); callers
        only ever ask "was it truncated", so those collapse to "length".
        """
        meta = getattr(message, "response_metadata", {}) or {}
        reason = str(meta.get("done_reason") or meta.get("stop_reason") or meta.get("finish_reason") or "stop")
        return "length" if reason.lower() in {"length", "max_tokens", "model_length"} else reason

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
            raise _failure(exc, "chiamata al modello fallita") from exc
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
            retry = self._without_thinking(exc)
            if retry is None:
                raise _failure(exc, "chiamata al modello fallita") from exc
            try:
                message = self._invoke(retry, self._messages(system, user), TEXT_TIMEOUT_SECONDS)
            except Exception as second:  # noqa: BLE001
                raise _failure(second, "chiamata al modello fallita") from second
        return self._checked_text(message, what="risposta del modello")

    def _without_thinking(self, error: Exception):
        """The same answer model without thinking, when the provider says it has none; None otherwise.

        A backstop for an Ollama too old to report capabilities. The replacement is kept, so the
        refusal costs one request, not one per call.
        """
        return None

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
            structured = self._structured_decision(schema)
            result = self._invoke(structured, self._messages(system, user), DECISION_TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001 - LLM failure falls back, never raises
            # Old driver without json_schema support: degrade to the plain
            # JSON channel and validate the menu locally, instead of failing
            # every decision into escalation. A timeout is not a missing
            # feature — it surfaces as a typed error, never a silent retry
            # that would double the hang.
            if not isinstance(exc, NotImplementedError):
                raise _failure(exc, "decisione del modello fallita") from exc
            try:
                fallback = self._invoke(self._plain_json_decision(), self._messages(system, user), deadline - time.monotonic())
            except Exception as fallback_exc:  # noqa: BLE001
                raise _failure(fallback_exc, "decisione del modello fallita") from fallback_exc
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


class ChatOllamaLLM(_ChatModelLLM):
    """Synchronous contract backed by cancellable LangChain/Ollama requests."""

    def __init__(self, cfg: LaneConfig) -> None:
        import asyncio

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
            # il CoT aiuta e non rompe nulla. Misurato sul golden set. Solo se il
            # modello sa pensare: Ollama risponde 400 "does not support thinking" a
            # reasoning=True su un modello che non lo ha (granite, qwen2.5-coder, molti
            # 12B), e prima di questo controllo ogni modello senza thinking rendeva
            # inutilizzabile l'intera lane.
            capabilities = _ollama_capabilities(host, cfg.answer_tag)
            thinks = capabilities is None or "thinking" in capabilities
            self._text_model = ChatOllama(reasoning=True if thinks else None, **answer_common)
            self._answer_common = answer_common
            # Decisioni SENZA pensiero: il canale e' uno schema forzato
            # (json_schema) e il think ci va a cazzotti — misurato: 12/16
            # e p95 decisione da 5s a 170s. Scelta secca, niente rimuginii.
            self._decision_model = ChatOllama(reasoning=False, **decision_common)
        except TypeError:  # older driver without reasoning/validate switches
            self._json_model = ChatOllama(format="json", **router_common)
            self._text_model = ChatOllama(**answer_common)
            self._decision_model = ChatOllama(**decision_common)

    def _structured_decision(self, schema: dict):
        return self._decision_model.with_structured_output(schema, method="json_schema", include_raw=True)

    def _plain_json_decision(self):
        return self._decision_model.bind(format="json")

    def _without_thinking(self, error: Exception):
        if "does not support thinking" not in str(error) or not hasattr(self, "_answer_common"):
            return None
        from langchain_ollama import ChatOllama

        self._text_model = ChatOllama(**self._answer_common)
        return self._text_model


class ChatModelLLM(_ChatModelLLM):
    """The same contract over any provider LangChain can talk to: a frontier model through its API.

    `spec` is LangChain's `provider:model` (`anthropic:claude-sonnet-5-5`, `openai:...`), resolved by
    `init_chat_model`. The provider's own package (`langchain-anthropic`, `langchain-openai`) is an
    optional extra, loaded only here, so a machine that runs the lane on a local model never needs it.
    All three channels use the same model; their temperatures and output budgets differ the way the
    local ones do.
    """

    def __init__(self, cfg: LaneConfig, spec: str, *, model_factory=None) -> None:
        import asyncio

        provider, _, model = spec.partition(":")
        if not provider or not model:
            raise LLMError(f"modello non valido '{spec}': serve <provider>:<modello>, per esempio anthropic:claude-sonnet-5-5")
        factory = model_factory or self._init_chat_model
        self.spec = spec
        self._runner = asyncio.Runner()
        self._invoke_lock = threading.Lock()
        self._finalizer = weakref.finalize(self, _close_runtime, self._runner, None)
        common = {"timeout": TEXT_TIMEOUT_SECONDS}
        self._json_model = factory(spec, temperature=0.0, max_tokens=512, **common)
        self._text_model = factory(spec, temperature=cfg.temperature, max_tokens=2048, **common)
        self._decision_model = factory(spec, temperature=0.0, max_tokens=256, **common)

    def _check_outbound(self, messages) -> None:
        """What the lane reads (mail, notes, files) goes to a third party here, so a credential in it must not.

        The shape check is the safety net, not the policy: choosing a frontier model is what decides that
        the content may leave. A prompt carrying a provider token or a private key block is refused whole.
        """
        from nexgen_core import secret_shapes

        for message in messages:
            text = getattr(message, "content", "")
            text = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False)
            if secret_shapes.PEM_BLOCK.search(text) or secret_shapes.PROVIDER_TOKEN.search(text):
                raise LLMError(
                    "richiesta non inviata a un modello esterno: contiene qualcosa che ha la forma di una credenziale"
                )

    @staticmethod
    def _init_chat_model(spec: str, **kwargs):
        try:
            from langchain.chat_models import init_chat_model
        except ImportError as exc:
            raise LLMError(
                "driver mancante per un modello esterno: installa il pacchetto 'langchain' e quello del provider "
                "(per esempio langchain-anthropic), oppure usa un modello locale"
            ) from exc
        try:
            return init_chat_model(spec, **kwargs)
        except ImportError as exc:
            raise LLMError(f"manca il pacchetto del provider per '{spec}': {exc}") from exc


#: Env var that switches the lane to a frontier model: `provider:model`. Unset = the local Ollama pair.
LANE_MODEL_ENV = "NEXGEN_LANE_MODEL"


def build_llm(cfg: LaneConfig) -> LLM:
    """The model the lane runs on: a frontier model when `NEXGEN_LANE_MODEL` names one, else the local pair."""
    spec = (os.environ.get(LANE_MODEL_ENV) or "").strip()
    if spec:
        return ChatModelLLM(cfg, spec)
    return ChatOllamaLLM(cfg)
