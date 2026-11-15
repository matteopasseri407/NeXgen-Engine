"""The per-call context cap one agent may opt into, written as `contextTokens`.

One job: turn `context_tokens: N` on an agent into the
`models.providers.<provider>.models[].contextTokens` cap for that agent's model.
Why it exists: on the `claude-cli` road the cap becomes Claude Code's
`CLAUDE_CODE_AUTO_COMPACT_WINDOW` (`docs.openclaw.ai/gateway/cli-backends`, pinned
release), which is what makes the harness compact instead of growing towards a
1,000,000-token worst case; 74 model calls at a 200,000 cap is 14.8 M tokens
instead of 74 M. It is the L2 lever of `docs/proposals/daru-burn.md`.

Two rules are not preferences:

- nothing is written unless the agent says `context_tokens`, because this trades
  something real: an older conversation becomes a summary sooner;
- only `contextTokens` is written, never `contextWindow`. The cap is the active
  input of one call; the native window is the model's and inventing it would be a
  number this engine cannot know. That is also why `docs/squad.md` calls it
  experimental: the pinned release documents that an authored row merges with a
  bundled plugin's catalogue and that only a provider-level `baseUrl` pointing
  elsewhere excludes it, which this module never writes, but no live turn on this
  machine has been watched to confirm the plugin's auth route survives.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from nexgen_core.openclaw.claude_cli import provider_model_entry, split_ref

#: The squad key that opts one agent in.
CAP_KEY = "context_tokens"


def declared_cap(spec: Mapping[str, Any]) -> int | None:
    """The cap one agent declares in tokens, or None when it declares nothing usable.

    A bool is not a token count (`True` is an `int` in Python) and a value that is
    not a positive whole number would be a request the gateway cannot honour, so
    both are ignored rather than guessed at.
    """
    value = spec.get(CAP_KEY)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def caps(squad: Mapping[str, Any] | None) -> dict[tuple[str, str], int]:
    """The cap of every model the squad asks to bound, keyed by (provider, model).

    Two agents naming the same model with different numbers are one model with one
    cap, so the smallest wins: that is the only choice that keeps both promises,
    and it is stable whatever order the file lists the agents in.
    """
    agents = (squad or {}).get("agents")
    if not isinstance(agents, Mapping):
        return {}
    wanted: dict[tuple[str, str], int] = {}
    for spec in agents.values():
        if not isinstance(spec, Mapping):
            continue
        cap = declared_cap(spec)
        model = spec.get("model")
        if isinstance(model, Mapping):
            model = model.get("primary")
        provider, name = split_ref(model.strip() if isinstance(model, str) else "")
        if cap is None or not provider or not name:
            continue
        key = (provider, name)
        wanted[key] = min(cap, wanted[key]) if key in wanted else cap
    return wanted


def apply(config: dict[str, Any], squad: Mapping[str, Any] | None) -> None:
    """Writes each cap into `models.providers.<provider>.models[]`, merging by id.

    The list is keyed by `id` the way OpenClaw stores it, so a model the person or
    another pass already declared keeps every field it carried and gains the cap,
    and a provider block that did not exist is created with the one entry.
    """
    wanted = caps(squad)
    if not wanted:
        return
    models = dict(config.get("models") or {})
    providers = dict(models.get("providers") or {})
    for (provider, name), cap in wanted.items():
        block = dict(providers.get(provider) or {})
        declared = block.get("models")
        entries = list(declared) if isinstance(declared, list) else []
        existing = provider_model_entry(block, provider, name)
        if existing is None:
            entries.append({"id": name, "contextTokens": cap})
        else:
            # A new dict per entry, so the caller's configuration is not edited
            # through a list the proposal shares with it.
            index = next(position for position, item in enumerate(entries) if item is existing)
            entries[index] = {**existing, "contextTokens": cap}
        block["models"] = entries
        providers[provider] = block
    models["providers"] = providers
    config["models"] = models
