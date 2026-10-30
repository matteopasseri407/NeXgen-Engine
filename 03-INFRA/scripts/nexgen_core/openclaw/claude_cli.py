"""Which agents OpenClaw runs through Claude Code's own CLI, and on which model.

One job: answer "does this agent run on `claude-cli`?" from a configuration dict,
with the precedence OpenClaw's own resolver applies (`resolveModelRuntimePolicy` in
its `model-runtime-policy` module): the agent's own `models` entry, then
`agents.defaults.models`, then the provider's `models[]` entry, then the agent's
`provider/*` wildcard, then the provider's own `agentRuntime`. Two decisions depend
on this one answer -- the connectors kept off the loopback and the gateway that serves
them instead -- so it is answered here once, as a pure function of the file, and nowhere
else.

Why it matters: for a `claude-cli` agent OpenClaw hands Claude Code an MCP server
of its own named `openclaw`, marked `alwaysLoad: true`, carrying every tool the
agent has, connectors included. `alwaysLoad` puts every schema in the prompt of
every call: on a configured gateway that measured 943k tokens for one request,
74 calls in one turn. A `claude-cli` agent belongs on the engine's `lazy-mcp`
gateway, which `claude_loopback` keeps off the deny list, and `claude_native` keeps
Claude Code's own file aligned for the sessions that read it directly.
"""
from __future__ import annotations

from typing import Any

#: The provider whose models can be routed to Claude Code's CLI. The requirement
#: is deliberately about Anthropic models only: a `claude-cli` pin on any other
#: provider is not a configuration this engine reads as Claude Code.
ANTHROPIC = "anthropic"

#: The runtime id OpenClaw uses for an agent driven by the Claude Code binary.
CLAUDE_CLI = "claude-cli"


def _dict(value: Any) -> dict[str, Any]:
    """The value when it is a dict, an empty one otherwise: a config is data, never a shape to trust."""
    return value if isinstance(value, dict) else {}


def _primary_ref(value: Any) -> str | None:
    """The primary ref of a `model` field: a plain string, or the `primary` of a chain."""
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        return _primary_ref(value.get("primary"))
    return None


def split_ref(ref: str) -> tuple[str, str]:
    """`anthropic/claude-opus-5-5` -> (`anthropic`, `claude-opus-5-5`).

    An auth-profile suffix (`@work`) is not part of the model, and a ref without a
    slash names no provider at all: both are answered honestly rather than guessed.
    """
    text = str(ref or "").strip()
    provider, sep, model = text.partition("/")
    if not sep:
        return "", text
    model = model.split("@", 1)[0].strip() if "@" in model else model.strip()
    return provider.strip().lower(), model


def provider_model_entry(provider_block: Any, provider: str, model_id: str) -> dict[str, Any] | None:
    """The `models.providers.<provider>.models[]` entry for one id, as OpenClaw matches it.

    Its own matcher accepts either the bare id or `provider/id` in that field, so
    both are matched here: a configuration written either way means the same thing.
    """
    for entry in _dict(provider_block).get("models") or []:
        if not isinstance(entry, dict):
            continue
        entry_id = str(entry.get("id") or "").strip()
        if entry_id == model_id:
            return entry
        entry_provider, entry_model = split_ref(entry_id)
        if entry_model == model_id and entry_provider == provider:
            return entry
    return None


def _runtime_of(entry: Any) -> str | None:
    """`agentRuntime.id` of one map entry, when it names one."""
    policy = _dict(_dict(entry).get("agentRuntime"))
    runtime = policy.get("id")
    return runtime.strip().lower() if isinstance(runtime, str) and runtime.strip() else None


def _policy_in(models: Any, provider: str, model_id: str, *, wildcard: bool) -> str | None:
    """The runtime id one `models` map pins for this model, exact key or `provider/*`."""
    for key, entry in _dict(models).items():
        key_provider, key_model = split_ref(str(key))
        if wildcard:
            if key_model != "*" or key_provider != provider:
                continue
        elif not (str(key).strip() == model_id or (key_provider == provider and key_model == model_id)):
            continue
        runtime = _runtime_of(entry)
        if runtime:
            return runtime
    return None


def agent_ref(config: dict[str, Any], agent_id: str) -> str | None:
    """The model ref one agent runs: its own, or the inherited default beside it."""
    agents = _dict(config.get("agents"))
    entry = _dict(_dict(agents.get("entries")).get(agent_id))
    return _primary_ref(entry.get("model")) or _primary_ref(_dict(agents.get("defaults")).get("model"))


def runtime_id(config: dict[str, Any], agent_id: str, ref: str) -> str | None:
    """The `agentRuntime.id` OpenClaw resolves for this agent and model, or None.

    The order is OpenClaw's, not a choice here: an agent's own pin wins over the
    defaults, a model entry over the agent's wildcard, and the provider's own
    `agentRuntime` is the last word before the default runtime applies.
    """
    provider, model_id = split_ref(ref)
    agents = _dict(config.get("agents"))
    defaults_models = _dict(agents.get("defaults")).get("models")
    agent_models = _dict(_dict(agents.get("entries")).get(agent_id)).get("models")
    provider_block = _dict(_dict(_dict(config.get("models")).get("providers")).get(provider))

    for models in (agent_models, defaults_models):
        pinned = _policy_in(models, provider, model_id, wildcard=False)
        if pinned:
            return pinned
    entry = provider_model_entry(provider_block, provider, model_id)
    pinned = _runtime_of(entry)
    if pinned:
        return pinned
    for models in (agent_models, defaults_models):
        pinned = _policy_in(models, provider, model_id, wildcard=True)
        if pinned:
            return pinned
    return _runtime_of(provider_block)


def is_claude_cli(config: dict[str, Any], agent_id: str) -> bool:
    """Whether this agent runs an Anthropic model through Claude Code's own CLI."""
    ref = agent_ref(config, agent_id)
    if not ref:
        return False
    provider, _ = split_ref(ref)
    return provider == ANTHROPIC and runtime_id(config, agent_id, ref) == CLAUDE_CLI


def claude_cli_agents(config: dict[str, Any]) -> list[str]:
    """Every agent in this configuration that runs on `claude-cli`, in the file's order."""
    entries = _dict(_dict(config.get("agents")).get("entries"))
    return [str(agent_id) for agent_id in entries if is_claude_cli(config, str(agent_id))]
