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
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nexgen_core.i18n import detect_language
from nexgen_core.openclaw.acp import SVILUPPO_ID, SVILUPPO_NAMES, acp_block, acpx_entry, sviluppo_enabled
from nexgen_core.openclaw.detect import Detection, LocalModelInfo

#: The top-level sections the engine owns. Everything else in `openclaw.json` is
#: OpenClaw's and the person's, and a plan names it as "yours, left untouched".
#: A section is owned even when this machine proposes nothing inside it today:
#: that is how a key the engine used to write is reported instead of forgotten.
OWNED_SECTIONS = ("plugins", "models", "agents", "tools", "gateway", "acp")

#: Plugins every OpenClaw gets: OpenClaw's own, with no subscription behind them.
BASE_PLUGINS = (
    "browser", "canvas", "device-pair", "document-extract", "file-transfer",
    "memory-core", "web-readability", "talk-voice", "tts-local-cli",
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

#: What a local model may still do once caged: read the conversation, look at an
#: image, read a PDF, speak, and search the Vault. Nothing that writes.
LOCAL_SAFE_TOOLS = ("read", "view_image", "pdf", "tts", "memory_search", "memory_get")

#: Denied to every local model. The groups are whole families of capability, so
#: a new tool in one of them is covered before anyone has to notice it exists.
LOCAL_DENY = (
    "group:web", "group:runtime", "group:ui", "group:nodes", "group:automation",
    "write", "edit", "apply_patch",
    "playwright__*", "lazy-mcp__lazy_mutate",
)

#: MCP servers whose tools write, and the names of those tools, expressed once as
#: data. OpenClaw names them `server__tool`, so a pattern here reads as "no
#: workflow creation, no note writing, no upload" without enumerating every tool.
#: `firecrawl` is here for the tools that fetch or drive a page: search and the
#: plain reads stay allowed, scraping and browsing do not.
WRITE_TOOL_TABLE: dict[str, tuple[str, ...]] = {
    "n8n-mcp": (
        "create_*", "delete_*", "update_*", "publish_*", "unpublish_*",
        "execute_*", "test_*", "validate_*", "mutate_*", "import_*", "archive_*",
    ),
    "vault-library": ("create_note", "append_note", "update_note", "update_section"),
    "drive": ("drive_propose_upload", "drive_confirm_upload"),
    "firecrawl": (
        "*scrape*", "*map*", "*extract*", "*crawl*", "*agent*", "*browser*", "*interact*",
    ),
}

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
    #: The language of the agent identities; None means the engine's own.
    lang: str | None = None
    #: The OpenCode agent, on unless the person asked for it to be left out.
    sviluppo: bool = True


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


def _language(chosen: Choices) -> str:
    """`it` or `en`, from the caller or from the engine's own decision."""
    value = (chosen.lang or detect_language() or "en").strip().lower()
    return "it" if value.startswith("it") else "en"


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
        detected.local.llamacpp, "llamacpp",
        detected.local.llamacpp_url.rstrip("/") + "/v1", "openai-completions",
    )
    return tuple(models)


def _plugin_allow(detected: Detection, choices: Choices) -> list[str]:
    """The plugins this machine can actually use, and never a Google one."""
    allow = list(BASE_PLUGINS)
    if detected.claude.available:
        allow.append("anthropic")
    if detected.codex.available:
        allow.extend(["codex", "openai"])
    if detected.opencode_go:
        allow.append("opencode-go")
    if detected.local.ollama:
        allow.append("ollama")
    if choices.acpx or sviluppo_enabled(detected, choices.sviluppo):
        allow.append("acpx")
    return allow


def _plugin_entries(detected: Detection, chosen: Choices) -> dict[str, Any]:
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
    if chosen.acpx or sviluppo_enabled(detected, chosen.sviluppo):
        entries["acpx"] = acpx_entry()
    return entries


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
    existing = _existing_model(current, model.provider, model.model_id)
    merged = dict(existing) if existing is not None else {}
    for key, value in proposed.items():
        merged.setdefault(key, value)
    return merged


def _provider_blocks(models: tuple[LocalModel, ...], current: dict[str, Any] | None) -> dict[str, Any]:
    """One provider per local backend, each with the models it reported."""
    providers: dict[str, Any] = {}
    for model in models:
        block = providers.setdefault(model.provider, {
            "baseUrl": model.base_url,
            "api": model.api,
            "models": [],
        })
        block["models"].append(_model_entry(model, current))
    return providers


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


def _sviluppo_entry(model: str, current: dict[str, Any] | None, state_dir: Path | None, language: str) -> dict[str, Any]:
    """The OpenCode agent: a real harness launched over ACP, one to one.

    `model.primary` is what selects the harness's model; without it OpenClaw
    runs whatever the harness defaults to, which is how an agent answered with
    the wrong model once already. `runtime.type: "acp"` is what makes the
    harness run at all rather than OpenClaw answering in its place.
    """
    name = SVILUPPO_NAMES[language]
    entry: dict[str, Any] = {"name": name, "identity": {"name": name}}
    workspace = _workspace(SVILUPPO_ID, current, state_dir)
    if workspace:
        entry["workspace"] = workspace
    entry["model"] = {"primary": model}
    entry["runtime"] = {
        "type": "acp",
        "acp": {"agent": "opencode", "backend": "acpx", "mode": "persistent"},
    }
    return entry


def _agent_entries(models: dict[str, str], detected: Detection, current: dict[str, Any] | None, language: str) -> dict[str, Any]:
    """The agents to create, with only the options that belong to the model chosen."""
    state_dir = _state_dir(detected)
    entries: dict[str, Any] = {}
    for agent_id, model in models.items():
        if agent_id == SVILUPPO_ID:
            entries[agent_id] = _sviluppo_entry(model, current, state_dir, language)
            continue
        entry: dict[str, Any] = {
            "name": agent_id,
            "identity": {"name": IDENTITY_NAMES[language][agent_id]},
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


def denied_tools() -> tuple[str, ...]:
    """Every tool pattern a local model may not call, base families plus writer tools."""
    patterns = list(LOCAL_DENY)
    for server, names in WRITE_TOOL_TABLE.items():
        patterns.extend(f"{server}__{name}" for name in names)
    return tuple(patterns)


def _cage(models: tuple[LocalModel, ...]) -> dict[str, Any]:
    """The cage around each local model: read and speak, never write."""
    deny = list(denied_tools())
    return {
        model.ref: {
            "profile": "messaging",
            "alsoAllow": list(LOCAL_SAFE_TOOLS),
            "deny": list(deny),
        }
        for model in models
    }


def _agent_pairs(models: dict[str, str]) -> dict[str, list[str]]:
    """Which agents may message which, for the agents created: id -> the ids it may send to.

    OpenClaw takes the pairs in two places, not one: `tools.agentToAgent.allow` lists
    every agent that takes part (ids, not pairs: a list of lists is refused at start),
    and `agents.entries.<id>.tools.agentToAgent.send` narrows whom each one may reach.
    Never an empty mapping entry: an agent with no partner is simply left out.
    """
    edges: list[tuple[str, str]] = []
    if "main" in models and "capo" in models:
        edges.append(("main", "capo"))
    if "main" in models and "automazioni" in models:
        edges.append(("main", "automazioni"))
    if SVILUPPO_ID in models:
        for other in ("main", "capo"):
            if other in models:
                edges.append((other, SVILUPPO_ID))
    partners: dict[str, list[str]] = {}
    for left, right in edges:
        partners.setdefault(left, []).append(right)
        partners.setdefault(right, []).append(left)
    return partners


def _gateway(chosen: Choices, current: dict[str, Any] | None) -> dict[str, Any]:
    """The gateway block, keeping a token the file already has."""
    gateway = current.get("gateway") if isinstance(current, dict) else None
    auth = gateway.get("auth") if isinstance(gateway, dict) else None
    token = auth.get("token") if isinstance(auth, dict) else None
    return {
        "mode": "local",
        "port": GATEWAY_PORT,
        "bind": "lan" if chosen.lan else "loopback",
        "auth": {
            "mode": "token",
            "token": token if isinstance(token, str) and token.strip() else PENDING_TOKEN,
        },
        "tailscale": {"mode": "off"},
    }


def build_config(
    detected: Detection,
    choices: Choices | None = None,
    current: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The sections the engine owns, and nothing else.

    `current` is the configuration in force, as a value. It never changes what
    the engine wants, only what it must not overwrite: an existing gateway
    token, an existing agent workspace, the fields of a model block that is
    already there.
    """
    chosen = choices or Choices()
    models = local_models(detected)
    agents = agent_models(detected, chosen)
    language = _language(chosen)

    tools: dict[str, Any] = {
        # With small models, tool search fails; a fixed small list works.
        "toolSearch": False,
        "exec": {"security": "allowlist", "ask": "on-miss"},
        "byProvider": _cage(models),
    }
    partners = _agent_pairs(agents)
    if partners:
        tools["agentToAgent"] = {"enabled": True, "allow": sorted(partners)}

    config: dict[str, Any] = {
        "plugins": {
            "allow": _plugin_allow(detected, chosen),
            "entries": _plugin_entries(detected, chosen),
        },
        "agents": {
            "ownership": "explicit",
            "defaults": {
                "models": _default_models(detected),
                # Otherwise the system jobs write to the channels continuously.
                "heartbeat": {"every": "0m"},
            },
            "entries": _agent_entries(agents, detected, current, language),
        },
        "tools": tools,
        "gateway": _gateway(chosen, current),
    }
    for agent_id, entry in config["agents"]["entries"].items():
        if agent_id in partners:
            entry["tools"] = {"agentToAgent": {"send": partners[agent_id]}}
    acp = acp_block(detected, sviluppo=chosen.sviluppo, acpx=chosen.acpx)
    if acp is not None:
        config["acp"] = acp
    providers = _provider_blocks(models, current)
    if providers:
        config["models"] = {"providers": providers}
    return config
