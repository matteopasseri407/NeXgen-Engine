"""Which models every agent's picker shows, on this machine.

One job: name the model providers this machine can actually run, and write them where
OpenClaw's picker reads them -- `agents.defaults.modelPolicy.allow` for the override
allowlist and `agents.defaults.models` for per-model entries -- extending what the file in
force already says and never narrowing it.

Two documented facts decide the shape:

- `agents.defaults.models` holds aliases, runtime pins and metadata; adding an entry there
  does not restrict model overrides.
- `agents.defaults.modelPolicy.allow` is the override allowlist, accepts trailing
  `provider/*` wildcards, and makes the picker read that provider's discovered catalogue.

So the engine registers a wildcard per reachable provider, not a frozen list of ids: a
provider that publishes a model tomorrow keeps it selectable without editing this file,
and a CLI backend plugin's models appear because their provider does. `model` on an agent
is only that agent's default; the list here is what it may be changed to.
"""
from __future__ import annotations

from typing import Any, Iterable

from nexgen_core.openclaw.agy_backend import approved
from nexgen_core.openclaw.terms import LOCAL_AGY_BACKEND

#: The provider every `claude-cli` route and every Anthropic model is named after.
CLAUDE_PROVIDER = "anthropic"
#: The provider the Codex backend and the `openai` plugin serve.
CODEX_PROVIDER = "openai"
#: The provider the flat OpenCode Go key serves.
GO_PROVIDER = "opencode-go"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _refs(value: Any) -> list[str]:
    """The model refs in one agent's `model`, as OpenClaw accepts them: a string or per-role keys."""
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, dict):
        return [ref for key in ("primary", "fallbacks") for ref in _refs(value.get(key))]
    if isinstance(value, list):
        return [ref for item in value for ref in _refs(item)]
    return []


def provider_of(ref: str) -> str:
    """`anthropic/claude-x` -> `anthropic`; text without a slash names no provider."""
    prefix, sep, _ = str(ref or "").strip().partition("/")
    return prefix if sep else ""


def wildcard(provider: str) -> str:
    """The allowlist form of "every model this provider offers"."""
    return f"{provider}/*" if provider else ""


def _unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


def allowed(config: dict[str, Any] | None) -> list[str]:
    """The override allowlist in force, exactly as written."""
    defaults = _dict(_dict(_dict(config).get("agents")).get("defaults"))
    values = _dict(defaults.get("modelPolicy")).get("allow")
    return [str(item) for item in values] if isinstance(values, list) else []


def named_providers(config: dict[str, Any] | None) -> list[str]:
    """Every provider the configuration already names, in any of the places OpenClaw reads one."""
    agents = _dict(_dict(config).get("agents"))
    defaults = _dict(agents.get("defaults"))
    found: list[str] = []
    for ref in _dict(defaults.get("models")):
        found.append(provider_of(ref))
    for key in _dict(_dict(config).get("models")).get("entries", {}):
        found.append(provider_of(key))
    for provider in _dict(_dict(config).get("models")).get("providers", {}):
        found.append(str(provider))
    for entry in _dict(agents.get("entries")).values():
        found.extend(provider_of(ref) for ref in _refs(_dict(entry).get("model")))
    for profile in _dict(_dict(_dict(config).get("auth")).get("profiles")).values():
        found.append(str(_dict(profile).get("provider") or ""))
    return _unique(found)


def reachable_wildcards(detected: Any, current: dict[str, Any] | None = None) -> list[str]:
    """One wildcard per provider this machine has a login or a backend for."""
    providers: list[str] = []
    if getattr(detected.claude, "available", False):
        providers.append(CLAUDE_PROVIDER)
    if getattr(detected.codex, "available", False):
        providers.append(CODEX_PROVIDER)
    if getattr(detected, "opencode_go", False):
        providers.append(GO_PROVIDER)
    local = getattr(detected, "local", None)
    if getattr(local, "ollama", ()):
        providers.append("ollama")
    if getattr(local, "llamacpp", ()):
        providers.append("llama-cpp")
    if approved(current):
        providers.append(LOCAL_AGY_BACKEND)
    return [wildcard(provider) for provider in providers]


def with_providers(config: dict[str, Any], providers: Iterable[str]) -> dict[str, Any]:
    """The same proposal with those providers selectable, on top of everything already allowed."""
    return _with_allow(config, _unique([*allowed(config), *providers]))


def _with_allow(config: dict[str, Any], refs: list[str]) -> dict[str, Any]:
    """The same proposal carrying that allowlist; an empty list writes nothing."""
    if not refs:
        return config
    agents = _dict(config.get("agents"))
    defaults = dict(_dict(agents.get("defaults")))
    defaults["modelPolicy"] = {**_dict(defaults.get("modelPolicy")), "allow": refs}
    return {**config, "agents": {**agents, "defaults": defaults}}


def with_models(config: dict[str, Any], refs: Iterable[str]) -> dict[str, Any]:
    """The same proposal with one `agents.defaults.models` entry per ref, so every picker lists it."""
    wanted = [ref for ref in _unique(str(item or "").strip() for item in refs) if provider_of(ref)]
    if not wanted:
        return config
    agents = _dict(config.get("agents"))
    defaults = dict(_dict(agents.get("defaults")))
    models = dict(_dict(defaults.get("models")))
    for ref in wanted:
        models.setdefault(ref, {})
    defaults["models"] = models
    return {**config, "agents": {**agents, "defaults": defaults}}


def with_reachable(config: dict[str, Any], detected: Any, current: dict[str, Any] | None = None) -> dict[str, Any]:
    """Every provider this machine can run is selectable, and no provider already named is lost.

    The file in force is read as well as the proposal: that is where the owner's own
    allowlist and any provider they added live, and a setup that dropped either would
    narrow a selection it was asked to widen.
    """
    current = _dict(current)
    refs = _unique([
        *allowed(current),
        *reachable_wildcards(detected, current),
        *(wildcard(provider) for provider in named_providers(current)),
        *(wildcard(provider) for provider in named_providers(config)),
    ])
    return _with_allow(config, refs)


def with_declared(config: dict[str, Any], refs: Iterable[str]) -> dict[str, Any]:
    """A model a squad declares is listed in every picker, and its provider stays selectable."""
    wanted = [ref for ref in _unique(str(item or "").strip() for item in refs) if provider_of(ref)]
    if not wanted:
        return config
    return with_providers(with_models(config, wanted), [wildcard(provider_of(ref)) for ref in wanted])
