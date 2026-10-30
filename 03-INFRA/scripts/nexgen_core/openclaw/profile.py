"""The configuration the engine owns, derived as a pure function.

`build_config` takes what was detected, what the person chose and the
configuration already in force, and returns ONLY the sections the engine owns.
Everything else in `openclaw.json` — the MCP servers, the channels, the rest of
OpenClaw's own settings — belongs to OpenClaw and to the person using it.

Nothing in this module reads a file, an environment variable or a clock: the
same inputs always produce the same dict, which is what lets `nexgen openclaw
plan` promise it will not write and lets B2 reuse the exact same answer. The
file in force arrives as a value (`current`), never as a path.

Four rules are not preferences, and each has its reason where it is applied: no
Google subscription ever enters OpenClaw (its terms forbid using a Google plan
outside Google's own clients), a local model always comes with a cage around its
tools, Ollama ids are written without `:latest` (OpenClaw fails to recognise the
suffixed id), and a model block carries its context window (without it OpenClaw
miscounts and overflows).

Two proposals are not about an agent at all and live in their own module:
`background` keeps the unattended jobs off a local model, and `observation` keeps
the gateway from watching the whole home. Both arrive as the `current` file and
the machine's platform, never as a read of either.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nexgen_core.i18n import detect_language
from nexgen_core.openclaw import agy_backend, background, model_policy, observation
from nexgen_core.openclaw.terms import LOCAL_AGY_BACKEND
from nexgen_core.openclaw.acp import SVILUPPO_ID, SVILUPPO_NAMES, acp_block, acpx_entry, sviluppo_enabled
from nexgen_core.openclaw.detect import Detection
from nexgen_core.openclaw.gateway_section import gateway_block
from nexgen_core.openclaw.local_models import local_models, provider_blocks
from nexgen_core.openclaw.tools_profile import (
    LOCAL_DENY,
    LOCAL_SAFE_TOOLS,
    SUPERVISOR_AGENT,
    SUPERVISOR_TOOLS,
    WRITE_TOOL_TABLE,
    allow_supervisor_tools,
    denied_tools,
    tools_block,
)

__all__ = [
    "Choices",
    "GATEWAY_PORT",
    "LOCAL_DENY",
    "LOCAL_SAFE_TOOLS",
    "PENDING_TOKEN",
    "SUPERVISOR_AGENT",
    "SUPERVISOR_TOOLS",
    "WRITE_TOOL_TABLE",
    "agent_models",
    "build_config",
    "denied_tools",
]

#: The top-level sections the engine owns. Everything else in `openclaw.json` is
#: OpenClaw's and the person's, and a plan names it as "yours, left untouched".
#: A section is owned even when this machine proposes nothing inside it today:
#: that is how a key the engine used to write is reported instead of forgotten.
OWNED_SECTIONS = ("plugins", "models", "agents", "tools", "skills", "gateway", "update", "acp", "bindings")

#: Plugins every OpenClaw gets: OpenClaw's own, with no subscription behind them.
BASE_PLUGINS = (
    "browser", "canvas", "device-pair", "document-extract", "file-transfer",
    "web-readability", "talk-voice", "tts-local-cli",
)

CLAUDE_MODEL = "anthropic/claude-opus-5-5"
CODEX_MODEL = "openai/gpt-6-luna"
GO_MAIN_MODEL = "opencode-go/deepseek-v4.1-flash"
GO_CAPO_MODEL = "opencode-go/deepseek-v4-pro"

#: The gateway's own port. Loopback by default; "lan" only for the phone app.
GATEWAY_PORT = 18789
#: Written as the token only when the file has none: an existing token is the
#: person's and is never replaced. A placeholder, never a secret.
PENDING_TOKEN = "generated-by-nexgen-setup"

#: How each agent introduces itself, in the language the engine speaks. The id
#: stays `main`/`capo`/`automazioni`; only the person-facing name changes.
IDENTITY_NAMES: dict[str, dict[str, str]] = {
    "it": {"main": "assistente", "capo": "capo", "automazioni": "automazioni"},
    "en": {"main": "assistant", "capo": "lead", "automazioni": "automations"},
}


@dataclass(frozen=True)
class Choices:
    """What only the person can decide; everything else is detected."""

    #: Reachable from the home network, which is what the phone app needs.
    lan: bool = False
    #: The acpx plugin, off unless asked for.
    acpx: bool = False
    #: A machine with no screen: the gateway stays on loopback and Tailscale Serve
    #: publishes it over HTTPS on the tailnet, so no port is ever opened.
    serve: bool = False
    #: The language of the agent identities; None means the engine's own.
    lang: str | None = None
    #: The OpenCode agent, on unless the person asked for it to be left out.
    sviluppo: bool = True


def _language(chosen: Choices) -> str:
    """`it` or `en`, from the caller or from the engine's own decision."""
    value = (chosen.lang or detect_language() or "en").strip().lower()
    return "it" if value.startswith("it") else "en"


def _llama_cpp_enabled(current: dict[str, Any] | None) -> bool:
    """Whether the person already switched OpenClaw's llama.cpp plugin on.

    The server may be off while the plugin stays on: turning it off because a
    backend is not answering would undo a choice that is not the engine's.
    """
    plugins = current.get("plugins") if isinstance(current, dict) else None
    if not isinstance(plugins, dict):
        return False
    allowed = plugins.get("allow") if isinstance(plugins.get("allow"), list) else []
    entries = plugins.get("entries") if isinstance(plugins.get("entries"), dict) else {}
    entry = entries.get("llama-cpp") if isinstance(entries.get("llama-cpp"), dict) else {}
    named = "llama-cpp" in {str(item) for item in allowed}
    return entry.get("enabled") is True or (named and entry.get("enabled") is not False)


def _agy_backend_kept(current: dict[str, Any] | None) -> bool:
    """The local agy CLI backend, the one Google road the owner approved, stays when already allowed."""
    return agy_backend.approved(current, LOCAL_AGY_BACKEND)


def _plugin_allow(detected: Detection, choices: Choices, current: dict[str, Any] | None = None) -> list[str]:
    """The plugins this machine can actually use; of Google's, only the approved agy backend already in force."""
    allow = list(BASE_PLUGINS)
    if detected.claude.available:
        allow.append("anthropic")
    if detected.codex.available:
        allow.extend(["codex", "openai"])
    if detected.opencode_go:
        allow.append("opencode-go")
    if detected.local.ollama:
        allow.append("ollama")
    if detected.local.llamacpp or _llama_cpp_enabled(current):
        allow.append("llama-cpp")
    if choices.acpx or sviluppo_enabled(detected, choices.sviluppo):
        allow.append("acpx")
    if _agy_backend_kept(current):
        allow.append(LOCAL_AGY_BACKEND)
    return allow


def _plugin_entries(
    detected: Detection, chosen: Choices, current: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Per-plugin settings: reuse the logins that already exist, invent none."""
    entries: dict[str, Any] = {}
    if detected.claude.available:
        entries["anthropic"] = {"config": {"sessionCatalog": {"enabled": False}}}
    if detected.codex.available:
        entries["codex"] = {
            "enabled": True,
            "config": {
                "appServer": {
                    "homeScope": "user",
                    "approvalPolicy": "on-request",
                    "sandbox": "workspace-write",
                    "approvalsReviewer": "user",
                },
            },
        }
    if detected.local.ollama:
        entries["ollama"] = {"enabled": True}
    if detected.local.llamacpp or _llama_cpp_enabled(current):
        entries["llama-cpp"] = {"enabled": True}
    if chosen.acpx or sviluppo_enabled(detected, chosen.sviluppo):
        entries["acpx"] = acpx_entry(detected)
    if _agy_backend_kept(current):
        # The owner already approved this backend: the engine aligns the one setting it
        # owns, and never enables or disables a plugin that is not its own.
        entries[LOCAL_AGY_BACKEND] = {
            "config": {"permissionMode": agy_backend.mode_for_engine(current, LOCAL_AGY_BACKEND)},
        }
    return _enabled_entries(entries, _plugin_allow(detected, chosen, current))


def _enabled_entries(entries: dict[str, Any], allow: list[str]) -> dict[str, Any]:
    """Switch on every plugin the allowlist names, which is what OpenClaw writes itself.

    An entry without `enabled` is a plugin OpenClaw repairs on the next `doctor --fix`;
    saying it here keeps the engine's proposal the one the file ends up holding.
    """
    return {
        name: {"enabled": True, **entry} if name in allow else entry
        for name, entry in entries.items()
    }


def _first_available(candidates: list[str | None]) -> str | None:
    """The first candidate that is actually usable, or None if none is."""
    for candidate in candidates:
        if candidate:
            return candidate
    return None


def agent_models(detected: Detection, choices: Choices | None = None) -> dict[str, str]:
    """The model each agent runs, in the order the engine prefers, for this machine."""
    local_refs = [model.ref for model in local_models(detected)]
    claude = CLAUDE_MODEL if detected.claude.available else None
    codex = CODEX_MODEL if detected.codex.available else None
    go = detected.opencode_go

    chosen: dict[str, str] = {}
    main = _first_available([GO_MAIN_MODEL if go else None, *local_refs, codex, claude])
    if main:
        chosen["main"] = main
    capo = _first_available([claude, codex, GO_CAPO_MODEL if go else None])
    if capo:
        chosen["capo"] = capo
    if detected.n8n:
        automazioni = _first_available([GO_CAPO_MODEL if go else None, claude])
        if automazioni:
            chosen["automazioni"] = automazioni
    if sviluppo_enabled(detected, (choices or Choices()).sviluppo):
        chosen[SVILUPPO_ID] = GO_MAIN_MODEL
    return chosen


def _state_dir(detected: Detection) -> Path | None:
    """OpenClaw's state folder: the folder the configuration file lives in."""
    return Path(detected.openclaw.config_path).parent if detected.openclaw.config_path else None


def _workspace(agent_id: str, current: dict[str, Any] | None, state_dir: Path | None) -> str | None:
    """Where this agent's files live: the existing folder, or a new absolute one.

    A relative path would anchor wherever OpenClaw happened to start, which is
    how `assistente` ended up pointing at nothing.
    """
    entries = current.get("agents", {}).get("entries", {}) if isinstance(current, dict) else {}
    existing = entries.get(agent_id) if isinstance(entries, dict) else None
    if isinstance(existing, dict) and isinstance(existing.get("workspace"), str) and existing["workspace"].strip():
        return existing["workspace"]
    if state_dir is None:
        return None
    return str(state_dir / "workspace" if agent_id == "main" else state_dir / f"workspace-{agent_id}")


def _existing_agent_name(current: dict[str, Any] | None, agent_id: str) -> str | None:
    if not isinstance(current, dict):
        return None
    entries = current.get("agents", {}).get("entries", {}) if isinstance(current.get("agents"), dict) else {}
    agent_entry = entries.get(agent_id) if isinstance(entries, dict) else None
    if not isinstance(agent_entry, dict):
        return None
    identity = agent_entry.get("identity") if isinstance(agent_entry.get("identity"), dict) else {}
    return identity.get("name") or agent_entry.get("name")


def _sviluppo_entry(model: str, current: dict[str, Any] | None, state_dir: Path | None, language: str) -> dict[str, Any]:
    """The OpenCode agent: a native agent on the Go key that drives real OpenCode.

    Not `runtime.type: "acp"`: OpenClaw routes an ACP agent to its harness only
    inside a bound session, and in a direct chat it answered with the default
    model (a local one) instead. The work on code still runs in OpenCode one to
    one: the agent opens it with `sessions_spawn` (runtime `acp`, agent
    `opencode`), as its manual says, and the session shows up in the panel.
    """
    existing_name = _existing_agent_name(current, SVILUPPO_ID)
    name = existing_name or SVILUPPO_NAMES[language]
    entry: dict[str, Any] = {"name": name, "identity": {"name": name}}
    workspace = _workspace(SVILUPPO_ID, current, state_dir)
    if workspace:
        entry["workspace"] = workspace
    entry["model"] = model
    return entry


def _tools_section(models: tuple[Any, ...], current: dict[str, Any] | None) -> dict[str, Any]:
    """The tools block, with the exec mode already decided: the one leaf the person owns."""
    tools = tools_block(models)
    tools["exec"] = _exec_policy(current)
    return tools


def _agents_section(
    models: tuple[Any, ...], detected: Detection, current: dict[str, Any] | None,
    language: str, agents: dict[str, str],
) -> dict[str, Any]:
    """The roster: who owns it, what every agent inherits, and the agents themselves."""
    return {
        "ownership": "explicit",
        "defaults": {
            **_default_model(agents, current),
            "models": _default_models(detected),
            # Otherwise the system jobs write to the channels continuously.
            "heartbeat": {"every": "0m"},
        },
        "entries": _agent_entries(agents, detected, current, language),
    }


def _default_model(agents: dict[str, str], current: dict[str, Any] | None) -> dict[str, Any]:
    """The `model` key of `agents.defaults`: the assistant's own model, unless the file decides.

    Left undeclared, OpenClaw picks a system primary itself, and on a machine with a
    local model it picked that one rather than the assistant's. A default the person set
    is theirs, in either shape the file stores it (`{primary, fallbacks}` or a plain ref),
    and an empty mapping leaves the key out of the proposal entirely.
    """
    block = current.get("agents") if isinstance(current, dict) else None
    defaults = block.get("defaults") if isinstance(block, dict) else None
    declared = defaults.get("model") if isinstance(defaults, dict) else None
    if (isinstance(declared, str) and declared.strip()) or (isinstance(declared, dict) and declared):
        return {}
    main = agents.get("main")
    return {"model": {"primary": main}} if main else {}


def _agent_entries(models: dict[str, str], detected: Detection, current: dict[str, Any] | None, language: str) -> dict[str, Any]:
    """The agents to create, with only the options that belong to the model chosen."""
    state_dir = _state_dir(detected)
    entries: dict[str, Any] = {}
    for agent_id, model in models.items():
        if agent_id == SVILUPPO_ID:
            entries[agent_id] = _sviluppo_entry(model, current, state_dir, language)
            continue
        existing_name = _existing_agent_name(current, agent_id)
        default_name = IDENTITY_NAMES[language].get(agent_id, agent_id)
        chosen_name = existing_name or default_name
        entry: dict[str, Any] = {
            "name": agent_id,
            "identity": {"name": chosen_name},
            "model": model,
        }
        workspace = _workspace(agent_id, current, state_dir)
        if workspace:
            entry["workspace"] = workspace
        if agent_id == "capo" and model == CLAUDE_MODEL:
            entry["thinkingDefault"] = "high"
            entry["runtime"] = {"type": "embedded"}
        entries[agent_id] = entry
    return entries


def _default_models(detected: Detection) -> dict[str, Any]:
    """The subscription models, each pointing at the runtime that already has the login."""
    models: dict[str, Any] = {}
    if detected.claude.available:
        models[CLAUDE_MODEL] = {"agentRuntime": {"id": "claude-cli"}}
    if detected.codex.available:
        models[CODEX_MODEL] = {"agentRuntime": {"id": "codex"}}
    return models


def _exec_policy(current: dict[str, Any] | None) -> dict[str, Any]:
    """The exec mode to propose: `auto`, and nothing at all when the file already decides.

    OpenClaw's normalized policy (`auto` is the classifier-reviewed miss) is the
    shape the engine wants for a machine that never chose one. A file that
    already carries a mode carries the person's decision, and it is also the only
    shape the schema accepts next to the legacy `security`/`ask` pair: proposing
    `mode` over it would loosen the approval policy and could produce a document
    `config validate` refuses.
    """
    tools = current.get("tools") if isinstance(current, dict) else None
    block = tools.get("exec") if isinstance(tools, dict) else None
    mode = block.get("mode") if isinstance(block, dict) else None
    return {} if isinstance(mode, str) and mode.strip() else {"mode": "auto"}


def _missing_channel_bindings(
    current: dict[str, Any] | None, entries: dict[str, Any],
) -> list[dict[str, Any]]:
    """The bindings OpenClaw needs for a configured channel it cannot route.

    With `agents.ownership: "explicit"` a channel without a binding reaches no
    agent. Only missing channels are proposed, bound to `main`; a channel the
    person already routed is never touched.
    """
    if not isinstance(current, dict):
        return []
    channels = current.get("channels") if isinstance(current.get("channels"), dict) else {}
    if not channels or not entries:
        return []
    bindings = current.get("bindings") if isinstance(current.get("bindings"), list) else []
    bound: set[str] = set()
    for binding in bindings:
        match = binding.get("match") if isinstance(binding, dict) else None
        channel = match.get("channel") if isinstance(match, dict) else None
        if isinstance(channel, str):
            bound.add(channel)
    agent = "main" if "main" in entries else sorted(entries, key=str)[0]
    missing: list[dict[str, Any]] = []
    for name, block in sorted(channels.items()):
        if not isinstance(block, dict) or not block or block.get("enabled") is False:
            continue
        if str(name) in bound or "*" in bound:
            continue
        missing.append({"agentId": agent, "match": {"channel": str(name), "accountId": "*"}})
    return missing


def _add_machine_rules(
    config: dict[str, Any],
    detected: Detection,
    agents: dict[str, str],
    current: dict[str, Any] | None,
    home: Path | None,
    platform: str | None,
) -> None:
    """What this machine needs rather than what an agent wants: the jobs' models, the watcher."""
    block = background.blocks(agents, current)
    entries = block.get("plugins", {}).get("entries", {})
    if entries:
        config["plugins"]["entries"].update(entries)
    defaults = block.get("agents", {}).get("defaults", {})
    if defaults:
        config["agents"]["defaults"].update(defaults)
    env = observation.env_block(_state_dir(detected), home, platform, current)
    if env is not None:
        config["env"] = env


def _update_policy() -> dict[str, Any]:
    """OpenClaw's own updater is off: the pin is the only owner of this version.

    Two updaters on one install is a tug of war, and the release the engine never
    tested against its plugin is the one that breaks it.
    """
    return {"auto": {"enabled": False}, "checkOnStart": False}


def build_config(
    detected: Detection,
    choices: Choices | None = None,
    current: dict[str, Any] | None = None,
    *,
    home: Path | None = None,
    platform: str | None = None,
) -> dict[str, Any]:
    """The sections the engine owns, and nothing else.

    `current` is the configuration in force, as a value. It never changes what
    the engine wants, only what it must not overwrite: an existing gateway
    token, an existing agent workspace, the fields of a model block that is
    already there. `home` and `platform` are the two facts the watcher decision
    needs; with no `home` nothing is proposed, which is the safe direction.
    """
    chosen = choices or Choices()
    models = local_models(detected)
    agents = agent_models(detected, chosen)
    language = _language(chosen)

    tools = _tools_section(models, current)

    config: dict[str, Any] = {
        "plugins": {
            "allow": _plugin_allow(detected, chosen, current),
            "entries": _plugin_entries(detected, chosen, current),
        },
        "agents": _agents_section(models, detected, current, language, agents),
        "tools": tools,
        "gateway": gateway_block(
            lan=chosen.lan, serve=chosen.serve, current=current,
            port=GATEWAY_PORT, pending_token=PENDING_TOKEN,
        ),
        "update": _update_policy(),
    }
    # Every model this machine can reach is selectable from any agent's picker; `model`
    # on an agent is only its default, and this changes none of them.
    config = model_policy.with_reachable(config, detected, current)
    allow_supervisor_tools(config["agents"]["entries"], current)
    acp = acp_block(detected, sviluppo=chosen.sviluppo, acpx=chosen.acpx)
    if acp is not None:
        config["acp"] = acp
    providers = provider_blocks(models, current)
    if providers:
        config["models"] = {"providers": providers}
    _add_machine_rules(config, detected, agents, current, home, platform)
    bindings = _missing_channel_bindings(current, config["agents"]["entries"])
    if bindings:
        config["bindings"] = bindings
    if not config["agents"]["entries"]:
        # OpenClaw refuses `agents` with no entry ("must contain at least one
        # configured agent"), and a proposal that cannot be written fresh would
        # make every setup fail on a machine with no model to give an agent.
        del config["agents"]
    return config
