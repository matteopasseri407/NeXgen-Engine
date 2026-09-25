"""Model access: a tiny interface, one LangChain implementation.

The lane only ever needs two verbs from a model: fill a JSON form (routing)
and answer in text from provided content. Keeping the interface this small
is what makes the framework replaceable: the graph and the engine talk to
``LLM``, not to LangChain.
"""
from __future__ import annotations

import json
import os
import re
from typing import Protocol

from .config import LaneConfig


class LLMError(RuntimeError):
    """The model could not be reached or its driver is missing."""


class LLM(Protocol):
    """What the lane needs from a model. Tests implement this with a fake."""

    def json(self, system: str, user: str) -> dict | None: ...

    def text(self, system: str, user: str) -> str: ...


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


class ChatOllamaLLM:
    """LLM backed by ``langchain_ollama.ChatOllama`` (optional dependency)."""

    def __init__(self, cfg: LaneConfig) -> None:
        try:
            from langchain_ollama import ChatOllama
        except ImportError as exc:  # pragma: no cover - exercised on machines without the extra
            raise LLMError(
                "dipendenza opzionale mancante: installa con  pip install 'nexgen-engine[local]'"
            ) from exc
        host = os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434"
        if "://" not in host:
            host = "http://" + host
        router_common = {
            "model": cfg.router_tag,
            "base_url": host,
            "temperature": 0.0,
            "num_ctx": min(cfg.num_ctx, ROUTER_NUM_CTX),
            "validate_model_on_init": False,
        }
        answer_common = {
            "model": cfg.answer_tag,
            "base_url": host,
            "temperature": cfg.temperature,
            "num_ctx": cfg.num_ctx,
            "validate_model_on_init": False,
        }
        try:
            self._json_model = ChatOllama(format="json", reasoning=False, **router_common)
            self._text_model = ChatOllama(reasoning=False, **answer_common)
        except TypeError:  # older driver without reasoning/validate switches
            self._json_model = ChatOllama(format="json", **router_common)
            self._text_model = ChatOllama(**answer_common)

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

    def json(self, system: str, user: str) -> dict | None:
        try:
            message = self._json_model.invoke(self._messages(system, user))
        except Exception as exc:  # noqa: BLE001 - surface a typed error upward
            raise LLMError(f"chiamata al modello fallita: {exc}") from exc
        return _json_block(self._content(message))

    def text(self, system: str, user: str) -> str:
        try:
            message = self._text_model.invoke(self._messages(system, user))
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"chiamata al modello fallita: {exc}") from exc
        return self._content(message)
