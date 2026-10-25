"""The models a configuration declares, judged by the doctor.

One job: read what the configuration says about models and say whether OpenClaw can
use it — ids it recognises, the cage around every local one, the subscription
behind a cloud one, and whether a person may pick a model other than the ones the
map already names. These checks were part of `doctor.py` until that file was full;
they are the model half of the same judgement, and they stay separate so the rules
about the gateway and about subscriptions do not have to share one file.

Read-only: nothing here runs a command or writes a file. The facts about this
machine's logins arrive in the detection snapshot, which `detect` fills once.
"""
from __future__ import annotations

from typing import Any

from nexgen_core.i18n import t
from nexgen_core.openclaw.detect import Detection
from nexgen_core.report import CheckOutcome, Severity

#: Which subscription a model provider depends on. Local providers are not here:
#: they are covered by the cage check, not by a login.
SUBSCRIPTION_LABELS = {
    "anthropic": "Claude",
    "openai": "Codex",
    "opencode-go": "the OpenCode Go key",
}


def agent_model_refs(config: dict[str, Any]) -> dict[str, str]:
    """Every agent's model, read from the string, object or ACP-primary shape."""
    entries = config.get("agents", {}).get("entries") if isinstance(config.get("agents"), dict) else None
    if not isinstance(entries, dict):
        return {}
    refs: dict[str, str] = {}
    for name, entry in entries.items():
        model = entry.get("model") if isinstance(entry, dict) else entry
        if isinstance(model, dict):
            # An ACP agent selects its harness model through `model.primary`.
            model = model.get("primary")
        if isinstance(model, str) and model.strip():
            refs[str(name)] = model.strip()
    return refs


def _provider_model_ids(config: dict[str, Any], provider: str) -> list[str]:
    """The ids a provider block declares, tolerating both shapes OpenClaw accepts."""
    providers = config.get("models", {}).get("providers") if isinstance(config.get("models"), dict) else None
    block = providers.get(provider) if isinstance(providers, dict) else None
    models = block.get("models") if isinstance(block, dict) else None
    if not isinstance(models, list):
        return []
    ids: list[str] = []
    for item in models:
        model_id = item.get("id") if isinstance(item, dict) else item
        if isinstance(model_id, str) and model_id:
            ids.append(model_id)
    return ids


def _local_refs(config: dict[str, Any]) -> list[str]:
    """Every local model the configuration can actually name, provider-qualified."""
    refs: list[str] = []
    for provider in ("ollama", "llama-cpp"):
        refs.extend(f"{provider}/{model_id}" for model_id in _provider_model_ids(config, provider))
    refs.extend(
        ref for ref in agent_model_refs(config).values()
        if ref.split("/", 1)[0] in ("ollama", "llama-cpp")
    )
    return sorted(set(refs))


def check_ollama_latest(config: dict[str, Any]) -> CheckOutcome:
    """OpenClaw does not recognise `:latest`, and then guesses the context window."""
    suffixed = [model_id for model_id in _provider_model_ids(config, "ollama") if model_id.endswith(":latest")]
    suffixed.extend(ref for ref in agent_model_refs(config).values() if ref.startswith("ollama/") and ref.endswith(":latest"))
    if suffixed:
        return CheckOutcome(
            id="openclaw.ollama_latest",
            severity=Severity.BROKEN,
            message=t("Ollama model ids carry ':latest', which OpenClaw does not recognise: {ids}",
                      ids=", ".join(sorted(set(suffixed)))),
            action=t("Write the id without ':latest' and run 'nexgen openclaw setup' again."),
        )
    return CheckOutcome(
        id="openclaw.ollama_latest",
        severity=Severity.OK,
        message=t("Every Ollama model id is written without ':latest'."),
    )


def check_cage(config: dict[str, Any]) -> CheckOutcome:
    """A local model without its cage is a model that can write the whole machine."""
    tools = config.get("tools") if isinstance(config.get("tools"), dict) else {}
    by_provider = tools.get("byProvider") if isinstance(tools.get("byProvider"), dict) else {}
    caged = [
        ref for ref in _local_refs(config)
        if isinstance(by_provider.get(ref), dict) and by_provider[ref].get("deny")
    ]
    missing = [ref for ref in _local_refs(config) if ref not in caged]
    if missing:
        return CheckOutcome(
            id="openclaw.cage",
            severity=Severity.BROKEN,
            message=t("This local model has no cage in tools.byProvider: {models}", models=", ".join(missing)),
            action=t("Run 'nexgen openclaw plan', then setup: a local model must not write, execute or browse."),
        )
    return CheckOutcome(
        id="openclaw.cage",
        severity=Severity.OK,
        message=t("Every local model is caged ({count}).", count=len(caged)),
    )


def check_agent_models(config: dict[str, Any], snapshot: Detection) -> CheckOutcome:
    """An agent on a subscription this machine does not have cannot answer."""
    available = {
        "anthropic": snapshot.claude.available,
        "openai": snapshot.codex.available,
        "opencode-go": snapshot.opencode_go,
    }
    unusable = []
    for agent, model in sorted(agent_model_refs(config).items()):
        provider = model.split("/", 1)[0]
        if provider in available and not available[provider]:
            unusable.append(f"{agent} -> {model} ({SUBSCRIPTION_LABELS[provider]})")
    if unusable:
        return CheckOutcome(
            id="openclaw.agent_models",
            severity=Severity.WARN,
            message=t("These agents use a subscription this machine does not have: {items}",
                      items="; ".join(unusable)),
            action=t("Log in to the subscription, or run 'nexgen openclaw plan' to see the model the engine would choose."),
        )
    return CheckOutcome(
        id="openclaw.agent_models",
        severity=Severity.OK,
        message=t("Every agent uses a model this machine can reach."),
    )


def check_model_overrides(config: dict[str, Any]) -> CheckOutcome:
    """Whether a person may pick any model this machine reaches, on any agent.

    `agents.defaults.models` is a model map: aliases, per-model settings, the
    runtime behind a subscription model. OpenClaw also reads it as an explicit
    override allowlist when `agents.defaults.modelPolicy` is absent, and then
    `/model`, a session override and every picker offer only those refs, on every
    agent that declares no policy of its own. The engine writes that map, so this
    is the shape most of its machines are in, and the refusal a person meets
    names the agent ("not allowed for this agent") while the cause is the map.

    Verified on the installed OpenClaw 2026.10.6: the legacy allowlist is built
    by `computeModelPolicyAllowlist` in `dist/agent-scope-config-CtLA1__T.mjs`,
    lines 71-81, and is replaced by the per-agent policy, then the default
    policy, then the map itself; `docs/concepts/models.md` lines 60, 73 and
    208-212 say the same thing, and an empty `modelPolicy: {}` keeps the policy
    unrestricted.
    """
    agents = config.get("agents") if isinstance(config.get("agents"), dict) else {}
    defaults = agents.get("defaults") if isinstance(agents.get("defaults"), dict) else {}
    if isinstance(defaults.get("modelPolicy"), dict):
        return CheckOutcome(
            id="openclaw.model_overrides",
            severity=Severity.OK,
            message=t("The model override policy is explicit, so the model map does not restrict it."),
        )
    models = defaults.get("models") if isinstance(defaults.get("models"), dict) else {}
    refs = sorted(str(key).strip() for key in models if str(key).strip())
    if not refs:
        return CheckOutcome(
            id="openclaw.model_overrides",
            severity=Severity.OK,
            message=t("No model override allowlist is in force: any model this machine reaches can be chosen."),
        )
    return CheckOutcome(
        id="openclaw.model_overrides",
        severity=Severity.WARN,
        message=t(
            "`agents.defaults.models` carries no `agents.defaults.modelPolicy`, so OpenClaw treats its "
            "{count} entries as the only models an agent may switch to: {refs}",
            count=len(refs), refs=", ".join(refs),
        ),
        action=t("Write `agents.defaults.modelPolicy.allow` with the exact refs or the `provider/*` entries a "
                 "person may choose, or an empty `modelPolicy: {}` to lift the list."),
    )
