"""Provider-keyed loopback tool deny policies for CLI runtimes.

One job: ensure OpenClaw's `tools.byProvider` carries loopback tool deny patterns
for every model-provider key routed to a native CLI runtime (`claude-cli`,
`google-antigravity-cli`), covering per-session model overrides and fallbacks so
connector schemas are not loaded into initial prompts with `alwaysLoad: true`.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from nexgen_core.openclaw.claude_cli import CLAUDE_CLI, split_ref
from nexgen_core.openclaw.claude_loopback import pattern_for, patterns
from nexgen_core.openclaw.native_connectors import ANTIGRAVITY_CLI, NATIVE_RUNTIMES
from nexgen_core.openclaw.setup_write import merged_document


def _runtime_of(entry: Any) -> str | None:
    """The runtime id pinned in an entry's `agentRuntime.id`, or None."""
    policy = entry.get("agentRuntime") if isinstance(entry, dict) else None
    runtime = policy.get("id") if isinstance(policy, dict) else None
    return runtime.strip().lower() if isinstance(runtime, str) and runtime.strip() else None


def _is_agy_ref(ref: Any) -> bool:
    """Whether a model ref or key names the google-antigravity-cli provider."""
    if not isinstance(ref, str):
        return False
    provider, _ = split_ref(ref)
    return provider == ANTIGRAVITY_CLI or ref.strip().lower() == ANTIGRAVITY_CLI


def _model_contains_agy(val: Any) -> bool:
    """Whether a `model` field (string or dict with primary/fallbacks) names agy."""
    if isinstance(val, str):
        return _is_agy_ref(val)
    if isinstance(val, dict):
        if _model_contains_agy(val.get("primary")):
            return True
        fallbacks = val.get("fallbacks")
        if isinstance(fallbacks, list):
            return any(_is_agy_ref(item) for item in fallbacks)
    return False


def _has_agy_ref(config: dict[str, Any]) -> bool:
    """Whether any model reference in the configuration names google-antigravity-cli."""
    models_sec = config.get("models") if isinstance(config.get("models"), dict) else {}
    for prov in (models_sec.get("providers") or {}):
        if _is_agy_ref(prov):
            return True
    agents_sec = config.get("agents") if isinstance(config.get("agents"), dict) else {}
    defaults = agents_sec.get("defaults") if isinstance(agents_sec.get("defaults"), dict) else {}
    if _model_contains_agy(defaults.get("model")):
        return True
    fallbacks = defaults.get("fallbacks")
    if isinstance(fallbacks, list) and any(_is_agy_ref(item) for item in fallbacks):
        return True
    for key in (defaults.get("models") or {}):
        if _is_agy_ref(key):
            return True
    entries = agents_sec.get("entries") if isinstance(agents_sec.get("entries"), dict) else {}
    for entry in entries.values():
        if not isinstance(entry, dict):
            continue
        if _model_contains_agy(entry.get("model")):
            return True
        entry_fallbacks = entry.get("fallbacks")
        if isinstance(entry_fallbacks, list) and any(_is_agy_ref(item) for item in entry_fallbacks):
            return True
        for key in (entry.get("models") or {}):
            if _is_agy_ref(key):
                return True
    return False


def _scan_models_maps(config: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    """Scan `agents.defaults.models` and `agents.entries.<id>.models` for native runtimes."""
    routed: dict[str, str] = {}
    selected_providers: dict[str, str] = {}
    agents_sec = config.get("agents") if isinstance(config.get("agents"), dict) else {}
    defaults = agents_sec.get("defaults") if isinstance(agents_sec.get("defaults"), dict) else {}
    models_maps = [defaults.get("models")]
    entries = agents_sec.get("entries") if isinstance(agents_sec.get("entries"), dict) else {}
    for entry in entries.values():
        if isinstance(entry, dict) and "models" in entry:
            models_maps.append(entry.get("models"))

    for models_map in models_maps:
        if not isinstance(models_map, dict):
            continue
        for key, model_entry in models_map.items():
            runtime = _runtime_of(model_entry)
            if runtime not in NATIVE_RUNTIMES:
                continue
            provider, model = split_ref(str(key))
            target_key = provider if model == "*" else (f"{provider}/{model}" if provider else str(key).strip())
            routed[target_key] = runtime
            if provider:
                selected_providers[provider] = runtime
    return routed, selected_providers


def _scan_providers(config: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    """Scan `models.providers` and their `models[]` items for native runtimes."""
    routed: dict[str, str] = {}
    selected_providers: dict[str, str] = {}
    models_sec = config.get("models") if isinstance(config.get("models"), dict) else {}
    providers = models_sec.get("providers") if isinstance(models_sec.get("providers"), dict) else {}
    for prov_name, prov_block in providers.items():
        if not isinstance(prov_block, dict):
            continue
        prov_key = str(prov_name).strip().lower()
        runtime = _runtime_of(prov_block)
        if runtime in NATIVE_RUNTIMES:
            routed[prov_key] = runtime
            selected_providers[prov_key] = runtime
        for item in (prov_block.get("models") or []):
            if not isinstance(item, dict):
                continue
            item_runtime = _runtime_of(item)
            if item_runtime in NATIVE_RUNTIMES:
                raw_id = str(item.get("id") or "").strip()
                _, m = split_ref(raw_id)
                model_name = m or raw_id
                target_key = f"{prov_key}/{model_name}"
                routed[target_key] = item_runtime
                selected_providers[prov_key] = item_runtime
    return routed, selected_providers


def routed_keys(config: dict[str, Any]) -> dict[str, str]:
    """Every model-provider key routed to a native CLI runtime, mapped to that runtime id."""
    r1_keys, r1_providers = _scan_models_maps(config)
    r2_keys, r2_providers = _scan_providers(config)
    routed = {**r1_keys, **r2_keys}
    selected_providers = {**r1_providers, **r2_providers}

    if _has_agy_ref(config):
        routed[ANTIGRAVITY_CLI] = ANTIGRAVITY_CLI
        selected_providers[ANTIGRAVITY_CLI] = ANTIGRAVITY_CLI

    tools_sec = config.get("tools") if isinstance(config.get("tools"), dict) else {}
    by_provider = tools_sec.get("byProvider") if isinstance(tools_sec.get("byProvider"), dict) else {}
    for existing_key in by_provider:
        prov, _ = split_ref(str(existing_key))
        if not prov:
            prov = str(existing_key).strip().lower()
        if prov in selected_providers:
            routed[str(existing_key)] = selected_providers[prov]

    return routed


def _entry_with_deny(existing_entry: Any, to_add: list[str]) -> dict[str, Any]:
    """A provider entry with loopback deny patterns merged into its existing deny list."""
    if isinstance(existing_entry, dict):
        existing_deny = existing_entry.get("deny")
        current_deny = [str(x) for x in existing_deny] if isinstance(existing_deny, list) else []
        merged_deny = sorted(set(current_deny) | set(to_add))
        return {**existing_entry, "deny": merged_deny}
    return {"deny": list(to_add)}


def apply(
    config: dict[str, Any],
    connectors: Iterable[str],
    agy_connectors: Iterable[str] | None = None,
    *,
    current: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a new configuration with `tools.byProvider` denying connectors on native CLI keys."""
    source = merged_document(current, config) if current is not None else config
    keys = routed_keys(source)
    if not keys:
        return config

    claude_patterns = patterns(connectors)
    agy_patterns = patterns(agy_connectors or [])

    tools_sec = config.get("tools") if isinstance(config.get("tools"), dict) else {}
    by_provider = tools_sec.get("byProvider") if isinstance(tools_sec.get("byProvider"), dict) else {}
    source_tools = source.get("tools") if isinstance(source.get("tools"), dict) else {}
    source_by_provider = source_tools.get("byProvider") if isinstance(source_tools.get("byProvider"), dict) else {}

    updated_by_provider = dict(by_provider)
    changed = False

    for key, runtime in keys.items():
        if runtime == CLAUDE_CLI:
            to_add = claude_patterns
        elif runtime == ANTIGRAVITY_CLI:
            to_add = agy_patterns
        else:
            continue
        if not to_add:
            continue

        existing_entry = by_provider.get(key)
        if existing_entry is None:
            existing_entry = source_by_provider.get(key)

        merged = _entry_with_deny(existing_entry, to_add)
        if merged != by_provider.get(key):
            changed = True
        updated_by_provider[key] = merged

    if not changed:
        return config

    return {
        **config,
        "tools": {
            **tools_sec,
            "byProvider": updated_by_provider,
        },
    }


def undeclared_provider_patterns(
    config: dict[str, Any],
    servers: Iterable[str],
    agy_connectors: Iterable[str] = (),
) -> list[str]:
    """The connectors routed provider keys can still reach through the loopback."""
    keys = routed_keys(config)
    if not keys:
        return []
    claude_wanted = [(server, pattern_for(server)) for server in servers]
    agy_wanted = [(server, pattern_for(server)) for server in agy_connectors]
    tools = config.get("tools") if isinstance(config.get("tools"), dict) else {}
    by_provider = tools.get("byProvider") if isinstance(tools.get("byProvider"), dict) else {}
    missing: list[str] = []
    for key, runtime in keys.items():
        wanted = claude_wanted if runtime == CLAUDE_CLI else agy_wanted
        entry = by_provider.get(key) if isinstance(by_provider.get(key), dict) else {}
        deny_list = entry.get("deny") if isinstance(entry.get("deny"), list) else []
        deny_set = {str(item) for item in deny_list}
        for server, pattern in wanted:
            if pattern and pattern not in deny_set:
                missing.append(f"{key}: {server}")
    return sorted(missing)
