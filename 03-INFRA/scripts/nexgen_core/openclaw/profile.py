"""The configuration the engine owns, derived as a pure function.

`build_config` takes what was detected and what the person chose, and returns
ONLY the sections the engine owns. Everything else in `openclaw.json` — the MCP
servers, the channels, the rest of OpenClaw's own settings — belongs to
OpenClaw and to the person using it, and is never described here.

Nothing in this module reads a file, an environment variable or a clock: the
same inputs always produce the same dict, which is what lets `nexgen openclaw
plan` promise it will not write and lets B2 reuse the exact same answer.

Three rules are not preferences, and each has its reason stated where it is
applied: no Google subscription ever enters OpenClaw (its terms forbid using a
Google plan outside Google's own clients), a local model always comes with a
cage around its tools, and Ollama ids are written without `:latest` (OpenClaw
does not recognise the suffixed id and then guesses the context window wrong).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nexgen_core.openclaw.detect import Detection

#: The top-level sections the engine owns. Everything else in `openclaw.json` is
#: OpenClaw's and the person's, and a plan names it as "yours, left untouched".
#: A section is owned even when this machine proposes nothing inside it today:
#: that is how a key the engine used to write is reported instead of forgotten.
OWNED_SECTIONS = ("plugins", "models", "agents", "tools", "gateway")

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
#: Written as the token until `nexgen openclaw setup` (B2) generates the real one.
#: A placeholder, never a secret: no credential value belongs in this dict.
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
WRITE_TOOL_TABLE: dict[str, tuple[str, ...]] = {
    "n8n-mcp": (
        "create_*", "delete_*", "update_*", "publish_*", "unpublish_*",
        "execute_*", "test_*", "validate_*", "mutate_*", "import_*", "archive_*",
    ),
    "vault-library": ("create_note", "append_note", "update_note", "update_section"),
    "drive": ("drive_propose_upload", "drive_confirm_upload"),
}


@dataclass(frozen=True)
class Choices:
    """What only the person can decide; everything else is detected."""

    #: Reachable from the home network, which is what the phone app needs.
    lan: bool = False
    #: The acpx plugin, off unless asked for.
    acpx: bool = False


@dataclass(frozen=True)
class LocalModel:
    """One model this machine serves, as the provider block will describe it."""

    provider: str
    model_id: str
    base_url: str
    api: str

    @property
    def ref(self) -> str:
        """`provider/model`, which is how agents and the cage name it."""
        return f"{self.provider}/{self.model_id}"


def _dedupe(values: list[str]) -> list[str]:
    """Keep the first occurrence of each name, in order."""
    seen: set[str] = set()
    kept: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            kept.append(value)
    return kept


def _without_latest(name: str) -> str:
    """Ollama's `:latest` is a tag, not part of the model's id."""
    return name.removesuffix(":latest")


def local_models(detected: Detection) -> tuple[LocalModel, ...]:
    """Every local model, with the id OpenClaw will recognise, Ollama first."""
    ollama = _dedupe([_without_latest(name) for name in detected.local.ollama])
    llamacpp = _dedupe([str(name) for name in detected.local.llamacpp])
    models = [
        LocalModel("ollama", name, detected.local.ollama_url.rstrip("/"), "ollama")
        for name in ollama
    ]
    models += [
        LocalModel("llamacpp", name, detected.local.llamacpp_url.rstrip("/") + "/v1", "openai-completions")
        for name in llamacpp
    ]
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
    if choices.acpx:
        allow.append("acpx")
    return allow


def _plugin_entries(detected: Detection) -> dict[str, Any]:
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
    return entries


def _provider_blocks(models: tuple[LocalModel, ...]) -> dict[str, Any]:
    """One provider per local backend, each listing the ids it reported."""
    providers: dict[str, Any] = {}
    for model in models:
        block = providers.setdefault(model.provider, {
            "baseUrl": model.base_url,
            "api": model.api,
            "models": [],
        })
        block["models"].append({"id": model.model_id})
    return providers


def _first_available(candidates: list[str | None]) -> str | None:
    """The first candidate that is actually usable, or None if none is."""
    for candidate in candidates:
        if candidate:
            return candidate
    return None


def agent_models(detected: Detection) -> dict[str, str]:
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
    return chosen


def _agent_entries(models: dict[str, str]) -> dict[str, Any]:
    """The agents to create, with only the options that belong to the model chosen."""
    entries: dict[str, Any] = {}
    if "main" in models:
        entries["main"] = {"model": models["main"], "workspace": "assistente"}
    if "capo" in models:
        capo: dict[str, Any] = {"model": models["capo"], "workspace": "capo"}
        if models["capo"] == CLAUDE_MODEL:
            capo["thinkingDefault"] = "high"
            capo["runtime"] = {"type": "embedded"}
        entries["capo"] = capo
    if "automazioni" in models:
        entries["automazioni"] = {"model": models["automazioni"], "workspace": "automazioni"}
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


def _agent_pairs(models: dict[str, str]) -> list[list[str]]:
    """Which agents may message which, in both directions, for the agents created.

    Never an empty list: without a second agent there is no conversation to
    allow, so the whole block is left out instead of declaring an empty one.
    """
    pairs: list[list[str]] = []
    if "main" in models and "capo" in models:
        pairs.extend([["main", "capo"], ["capo", "main"]])
    if "main" in models and "automazioni" in models:
        pairs.extend([["main", "automazioni"], ["automazioni", "main"]])
    return pairs


def build_config(detected: Detection, choices: Choices | None = None) -> dict[str, Any]:
    """The sections the engine owns, and nothing else."""
    chosen = choices or Choices()
    models = local_models(detected)
    agents = agent_models(detected)

    tools: dict[str, Any] = {
        # With small models, tool search fails; a fixed small list works.
        "toolSearch": False,
        "exec": {"security": "allowlist", "ask": "on-miss"},
        "byProvider": _cage(models),
    }
    pairs = _agent_pairs(agents)
    if pairs:
        tools["agentToAgent"] = {"enabled": True, "allow": pairs}

    config: dict[str, Any] = {
        "plugins": {
            "allow": _plugin_allow(detected, chosen),
            "entries": _plugin_entries(detected),
        },
        "agents": {
            "ownership": "explicit",
            "defaults": {
                "models": _default_models(detected),
                # Otherwise the system jobs write to the channels continuously.
                "heartbeat": {"every": "0m"},
            },
            "entries": _agent_entries(agents),
        },
        "tools": tools,
        "gateway": {
            "mode": "local",
            "port": GATEWAY_PORT,
            "bind": "lan" if chosen.lan else "loopback",
            "auth": {"mode": "token", "token": PENDING_TOKEN},
            "tailscale": {"mode": "off"},
        },
    }
    providers = _provider_blocks(models)
    if providers:
        config["models"] = {"providers": providers}
    return config
