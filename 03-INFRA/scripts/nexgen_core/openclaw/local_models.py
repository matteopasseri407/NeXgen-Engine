"""The local models this machine serves, and the provider blocks OpenClaw reads.

One job: turn what detection found into the `models.providers` shape, with the
id and the provider each backend really uses. Ollama is a plain provider;
llama.cpp is reached through OpenClaw's own `llama-cpp` plugin provider, so its
refs are `llama-cpp/<id>` and its block carries the private-network allowance
that plugin expects.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nexgen_core.openclaw.detect import Detection, LocalModelInfo

#: Provider-level settings OpenClaw's own plugin expects, per provider id.
#: `llama-cpp` is a plugin provider: it discovers and talks to llama-server
#: itself, and it needs the private-network allowance for a loopback endpoint.
PROVIDER_EXTRAS: dict[str, dict[str, Any]] = {
    "llama-cpp": {"request": {"allowPrivateNetwork": True}},
}

#: The providers a local model answers from: the backends this engine starts
#: and cages, and the only ones the evidence check watches.
LOCAL_PROVIDERS: tuple[str, ...] = ("ollama", "llama-cpp")


@dataclass(frozen=True)
class LocalModel:
    """One model this machine serves, as the provider block will describe it."""

    provider: str
    model_id: str
    base_url: str
    api: str
    context_window: int | None = None
    vision: bool = False
    reasoning: bool = False

    @property
    def ref(self) -> str:
        """`provider/model`, which is how agents and the cage name it."""
        return f"{self.provider}/{self.model_id}"


def _without_latest(name: str) -> str:
    """Ollama's `:latest` is a tag, not part of the model's id."""
    return name.removesuffix(":latest")


def _local_models(infos: tuple[LocalModelInfo, ...], provider: str, base_url: str, api: str) -> list[LocalModel]:
    """One provider's models, one per id, keeping the first report of each."""
    seen: set[str] = set()
    models: list[LocalModel] = []
    for info in infos:
        name = _without_latest(info.name) if provider == "ollama" else info.name
        if not name or name in seen:
            continue
        seen.add(name)
        models.append(LocalModel(
            provider=provider, model_id=name, base_url=base_url, api=api,
            context_window=info.context_window, vision=info.vision, reasoning=info.reasoning,
        ))
    return models


def local_models(detected: Detection) -> tuple[LocalModel, ...]:
    """Every local model, with the id OpenClaw will recognise, Ollama first."""
    models = _local_models(
        detected.local.ollama, "ollama", detected.local.ollama_url.rstrip("/"), "ollama",
    )
    models += _local_models(
        detected.local.llamacpp, "llama-cpp",
        detected.local.llamacpp_url.rstrip("/") + "/v1", "openai-completions",
    )
    return tuple(models)


def _existing_model(current: dict[str, Any] | None, provider: str, model_id: str) -> dict[str, Any] | None:
    """The entry for this id already in the file, when there is one."""
    models = current.get("models") if isinstance(current, dict) else None
    providers = models.get("providers") if isinstance(models, dict) else None
    block = providers.get(provider) if isinstance(providers, dict) else None
    listed = block.get("models") if isinstance(block, dict) else None
    if not isinstance(listed, list):
        return None
    for entry in listed:
        if isinstance(entry, dict) and str(entry.get("id", "")) == model_id:
            return entry
    return None


def _model_entry(model: LocalModel, current: dict[str, Any] | None) -> dict[str, Any]:
    """One full model block, merged over what the file already says about that id.

    A block with only `id` makes OpenClaw lose the context window, and it then
    miscounts and overflows. An existing entry keeps its fields: the engine only
    adds what is missing.
    """
    proposed: dict[str, Any] = {
        "id": model.model_id,
        "name": model.model_id,
        "reasoning": model.reasoning,
        "input": ["text", "image"] if model.vision else ["text"],
        "maxTokens": 8192,
        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
        "compat": {
            "supportsTools": True,
            "supportsUsageInStreaming": True,
            "supportsJsonSchemaResponseFormat": True,
        },
    }
    if model.context_window:
        proposed["contextWindow"] = model.context_window
        proposed["contextTokens"] = model.context_window
    if model.provider == "llama-cpp":
        # Its chat template compiles tool arguments into GBNF, where large
        # `pattern`/`maxLength` constraints break the build. OpenClaw's own
        # llama-cpp provider ships this cleaner; naming the profile keeps the
        # same guarantee if the block is read by a custom provider path.
        proposed["compat"]["toolSchemaProfile"] = "llamacpp"
    existing = _existing_model(current, model.provider, model.model_id)
    merged = dict(existing) if existing is not None else {}
    for key, value in proposed.items():
        merged.setdefault(key, value)
    return merged


def provider_blocks(models: tuple[LocalModel, ...], current: dict[str, Any] | None) -> dict[str, Any]:
    """One provider per local backend, each with the models it reported."""
    providers: dict[str, Any] = {}
    for model in models:
        block = providers.setdefault(model.provider, {
            **PROVIDER_EXTRAS.get(model.provider, {}),
            "baseUrl": model.base_url,
            "api": model.api,
            "models": [],
        })
        block["models"].append(_model_entry(model, current))
    return providers
