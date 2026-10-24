"""`nexgen openclaw plan`: what would change, and what stays the person's.

The plan is built from the same pure function B2 will use to write, and from a
read-only view of the file in force, so what it prints is what a setup would
produce. It writes nothing: the promise is not a convention here, it is what
the code can do — there is no write call in this module at all.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from nexgen_core import quota_policy
from nexgen_core.i18n import detect_language, t
from nexgen_core.openclaw import companion as app_gateway
from nexgen_core.openclaw import observation
from nexgen_core.openclaw.config_file import read_config
from nexgen_core.openclaw.detect import Detection, detect
from nexgen_core.openclaw.governor import with_chains
from nexgen_core.openclaw.profile import (
    CLAUDE_MODEL,
    CODEX_MODEL,
    GATEWAY_PORT,
    OWNED_SECTIONS,
    PENDING_TOKEN,
    SUPERVISOR_TOOLS,
    Choices,
    agent_models,
    build_config,
)
from nexgen_core.paths import openclaw_config, resolve_home
from nexgen_core.runtimes.openclaw import PLUGIN_ID

DISPLAY_NAMES = {"main": "assistente", "capo": "capo", "automazioni": "automazioni", "sviluppo": "sviluppo"}


def _with_engine_plugin(config: dict[str, Any], deposited: bool) -> dict[str, Any]:
    """The engine's guardrail plugin, enabled, and allowed to read the conversation.

    Enabling a plugin that is not there makes OpenClaw fail to start; the
    plugin is deposited by the runtime adapter, which is the only thing that
    knows how. The plan and the setup therefore share this one decision, so
    what the plan prints is what the setup writes.

    `hooks.allowConversationAccess` is the permission OpenClaw withholds from
    every plugin that is not bundled with it: without it a plugin is given no
    conversation hook at all. Two of the engine's are conversation hooks, and
    both go quiet without the grant: `before_model_resolve`, where the governor
    moves a turn off a spent plan, and the turn's messages and finished answer,
    which the evidence check judges. It is merged into whatever `hooks` block is
    already there: a key the person wrote is never removed, and a plugin older
    than these checks does not care that it is present. The plugin is the
    engine's own and already runs inside the gateway process; this is what makes
    its registration legal.
    """
    if not deposited:
        return config
    plugins = dict(config.get("plugins") or {})
    allow = list(plugins.get("allow") or [])
    if PLUGIN_ID not in allow:
        allow.append(PLUGIN_ID)
    entries = dict(plugins.get("entries") or {})
    entry = dict(entries.get(PLUGIN_ID) or {})
    hooks = dict(entry.get("hooks") or {}) if isinstance(entry.get("hooks"), dict) else {}
    hooks["allowConversationAccess"] = True
    entry.update({"enabled": True, "hooks": hooks})
    entries[PLUGIN_ID] = entry
    return {**config, "plugins": {**plugins, "allow": allow, "entries": entries}}


@dataclass(frozen=True)
class Plan:
    """The proposal, the file in force, and the difference between them."""

    detected: Detection
    choices: Choices
    config: dict[str, Any]
    path: Path
    current: dict[str, Any] | None = None
    current_error: str | None = None
    added: tuple[str, ...] = ()
    changed: tuple[str, ...] = ()
    kept: tuple[str, ...] = ()
    dropped: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)
    #: The fallback policy this machine declares, or an empty one. The setup writes its
    #: chains beside the plugin from this same value, so the two cannot disagree.
    failover: quota_policy.Failover = field(default_factory=quota_policy.Failover)

    @property
    def changes(self) -> dict[str, list[str]]:
        """Only what a setup would touch, plus what it would leave alone."""
        return {
            "added": list(self.added),
            "changed": list(self.changed),
            "kept": list(self.kept),
            "dropped": list(self.dropped),
        }

    def to_dict(self) -> dict[str, Any]:
        """The plan as JSON data, for `--json` and for B2."""
        return {
            "detected": self.detected.to_dict(),
            "choices": asdict(self.choices),
            "config": self.config,
            "agents": agent_models(self.detected, self.choices),
            "failover": self.failover.to_dict(),
            "current": {
                "path": str(self.path),
                "exists": self.path.is_file(),
                "valid": self.current is not None,
                "error": self.current_error,
            },
            "changes": self.changes,
            "notes": list(self.notes),
        }


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    """Every leaf of a nested dict, addressed as a dotted path."""
    if isinstance(value, dict):
        leaves: dict[str, Any] = {}
        for key, item in value.items():
            leaves.update(_flatten(item, f"{prefix}.{key}" if prefix else str(key)))
        return leaves
    return {prefix: value}


def _agent_notes(models: dict[str, str], detected: Detection, choices: Choices | None = None) -> list[str]:
    """Which agents the plan creates, and why one is missing when it is."""
    notes: list[str] = []
    if "main" not in models:
        notes.append(t("The assistant is not created: no model is available for it."))
    if "capo" not in models:
        notes.append(t("The code agent is not created: no model is available for it."))
    if detected.n8n and "automazioni" not in models:
        notes.append(t("The automation agent is not created: no model is available for it."))
    if not detected.n8n:
        notes.append(t("The automation agent is not created: the n8n module is not active on this machine."))
    note = _sviluppo_note(models, detected, choices or Choices())
    if note:
        notes.append(note)
    return notes


def _sviluppo_note(models: dict[str, str], detected: Detection, chosen: Choices) -> str | None:
    """Why the OpenCode agent is missing, said in the terms that can fix it."""
    if "sviluppo" in models:
        return None
    if not chosen.sviluppo:
        return t("The OpenCode agent is not created: setup was asked to leave it out.")
    if not detected.opencode:
        return t("The OpenCode agent is not created: the 'opencode' command is not on PATH.")
    if not detected.opencode_go:
        return t("The OpenCode agent is not created: no OpenCode Go key was detected.")
    return None


def _context_notes(config: dict[str, Any]) -> list[str]:
    """Models whose context window the backend did not report, and so is not written."""
    providers = config.get("models", {}).get("providers")
    if not isinstance(providers, dict):
        return []
    unknown: list[str] = []
    for block in providers.values():
        models = block.get("models") if isinstance(block, dict) else None
        for entry in models or []:
            if isinstance(entry, dict) and entry.get("id") and "contextWindow" not in entry:
                unknown.append(str(entry["id"]))
    if not unknown:
        return []
    return [t("The context window is unknown for these models and is not written: {models}",
              models=", ".join(unknown))]


def _build_notes(config: dict[str, Any], detected: Detection, choices: Choices | None = None) -> list[str]:
    """What a person has to know that the diff cannot say by itself."""
    notes: list[str] = []
    token = config.get("gateway", {}).get("auth", {}).get("token")
    if token == PENDING_TOKEN:
        notes.append(t("The gateway token is a placeholder: 'nexgen openclaw setup' generates the real one."))
    models = agent_models(detected, choices)
    notes.extend(_agent_notes(models, detected, choices))
    notes.extend(_context_notes(config))
    companion_note = _companion_note(detected)
    if companion_note:
        notes.append(companion_note)
    if models.get("capo") == CLAUDE_MODEL:
        notes.append(t("The code agent runs Claude with high reasoning and the embedded runtime."))
    if models.get("main") == CODEX_MODEL:
        notes.append(t("The assistant runs on Codex; only the models this account has are configured."))
    if not detected.local.available:
        notes.append(t(
            "No local model answered. The engine never downloads one: start it yourself, for example "
            "'ollama serve' or 'llama-server -m <model>.gguf --port 8080', then ask again."
        ))
    return notes


def _supervisor_tool_note(current: dict[str, Any] | None) -> str | None:
    """Say when the supervisor's tools cannot be allowlisted without touching the person's list.

    OpenClaw refuses `allow` and `alsoAllow` in the same scope, so an agent whose tools the
    person listed already gets nothing written: the plan names the tools to add there.
    """
    entries = current.get("agents", {}).get("entries") if isinstance(current, dict) else None
    entry = entries.get("capo") if isinstance(entries, dict) else None
    tools = entry.get("tools") if isinstance(entry, dict) else None
    if isinstance(tools, dict) and isinstance(tools.get("allow"), list):
        return t(
            "The supervisor agent has its own tools.allow: add {tools} there to let it drive the supervision.",
            tools=", ".join(SUPERVISOR_TOOLS),
        )


def _app_connection(port: int | None) -> str:
    """Which gateway the app's own settings name, when they name one."""
    if port is None:
        return ""
    if port == GATEWAY_PORT:
        return t(", and it is connected to this PC's gateway on port {port}", port=port)
    return t(", and it is connected to a gateway of its own on port {port}", port=port)


def _companion_note(detected: Detection) -> str | None:
    """The app's own gateway, named when it is here beside or instead of the native one."""
    gateway = detected.companion
    situation = gateway.situation(detected.openclaw.installed)
    if situation == app_gateway.APP:
        if gateway.is_isolated:
            return t("The OpenClaw Companion app points to its own isolated native gateway ({signals}): this engine "
                     "configures the native Node gateway, which is the road it supports, and cannot write the app's "
                     "isolated one. To use the app with the engine, open Connections in OpenClaw Companion and "
                     "choose 'a local Gateway on this PC'; the setup stops here until then, and runs beside the "
                     "app's gateway only with --node-gateway.", signals=", ".join(gateway.signals))
        return t("The OpenClaw Companion app runs the gateway on this PC ({signals}): this engine configures the "
                 "native Node gateway, which is the road it supports, and cannot write the app's one. To use the "
                 "app with the engine, open Connections in OpenClaw Companion and choose 'a local Gateway on this "
                 "PC'; the setup stops here until then, and runs beside the app's gateway only with --node-gateway.",
                 signals=", ".join(gateway.signals))
    if situation == app_gateway.BOTH:
        if gateway.is_isolated:
            return t("The OpenClaw Companion app points to its own isolated native gateway ({signals}){uses}: the setup "
                     "below writes the native one.", signals=", ".join(gateway.signals),
                     uses=_app_connection(gateway.port))
        return t("The OpenClaw Companion app has a gateway of its own on this PC ({signals}){uses}: the setup "
                 "below writes the native one.", signals=", ".join(gateway.signals),
                 uses=_app_connection(gateway.port))
    if situation == app_gateway.APP_REMOTE:
        return t("The OpenClaw Companion app is connected to a gateway elsewhere: nothing here is in conflict, "
                 "and the setup below writes the native gateway on this PC only.")
    if situation == app_gateway.NATIVE and gateway.names_no_gateway():
        return t("The OpenClaw Companion app is installed and names no gateway on this PC: once the setup below "
                 "has configured this PC's gateway, open Connections in OpenClaw Companion and choose 'a local "
                 "Gateway on this PC'.")
    return None


def _binding_note(config: dict[str, Any]) -> str | None:
    """Say which channels the setup binds, so the plan is not a silent config change."""
    bindings = config.get("bindings") if isinstance(config.get("bindings"), list) else []
    pairs = [
        f"{binding.get('match', {}).get('channel')} -> {binding.get('agentId')}"
        for binding in bindings
        if isinstance(binding, dict) and isinstance(binding.get("match"), dict)
    ]
    if not pairs:
        return None
    return t("The setup adds a binding for these channels: {pairs}.", pairs=", ".join(pairs))


def build_plan(
    detected: Detection | None = None,
    choices: Choices | None = None,
    *,
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
    engine_root: Path | None = None,
    vault_data: Path | None = None,
    plugin_deposited: bool | None = None,
    platform: str | None = None,
) -> Plan:
    """The proposal for this machine, diffed against the file in force."""
    snapshot = detected if detected is not None else detect(
        home=home, env=env, engine_root=engine_root, vault_data=vault_data,
    )
    chosen = choices or Choices(lang=detect_language())
    path = openclaw_config(home, env)
    home_dir = resolve_home(home)
    current, error = read_config(path)
    failover, failover_notes = quota_policy.load(home, vault=vault_data)
    proposed = build_config(snapshot, chosen, current, home=home_dir, platform=platform)
    config = with_chains(proposed, failover)
    deposited = plugin_deposited if plugin_deposited is not None else (path.parent / "extensions" / PLUGIN_ID).is_dir()
    config = _with_engine_plugin(config, deposited)

    proposed_flat = _flatten(config)
    current_flat = _flatten(current or {})
    owned = set(OWNED_SECTIONS)
    added = tuple(sorted(key for key in proposed_flat if key not in current_flat))
    changed = tuple(sorted(
        key for key, value in proposed_flat.items()
        if key in current_flat and current_flat[key] != value
    ))
    touched = {key.split(".", 1)[0] for key in proposed_flat}
    kept = tuple(sorted(key for key in (current or {}) if key not in owned and key not in touched))
    dropped = tuple(sorted(
        key for key in current_flat
        if key.split(".", 1)[0] in owned and key not in proposed_flat
    ))
    notes = list(_build_notes(config, snapshot, chosen))
    # A fallback line the engine cannot use is named here too: the plan is what a person
    # reads before the setup writes anything, so a typo in the policy must not be silent.
    notes.extend(failover_notes)
    polling_key = f"env.vars.{observation.POLLING_VAR}"
    if polling_key in added or polling_key in changed:
        notes.append(t("OpenClaw changes how it watches its configuration only when it starts: "
                       "restart the gateway once for this key to take effect."))
    binding_note = _binding_note(config)
    if binding_note:
        notes.append(binding_note)
    supervisor_note = _supervisor_tool_note(current)
    if supervisor_note:
        notes.append(supervisor_note)
    return Plan(
        detected=snapshot, choices=chosen, config=config, path=path,
        current=current, current_error=error,
        added=added, changed=changed, kept=kept, dropped=dropped,
        notes=tuple(notes), failover=failover,
    )


def _detected_lines(plan: Plan) -> list[str]:
    """What the engine found, one line per thing it can use or cannot."""
    found = plan.detected
    lines: list[str] = []
    openclaw = found.openclaw
    if openclaw.installed:
        lines.append(t("OpenClaw {version} ({path})", version=openclaw.version or "?", path=openclaw.config_path))
    else:
        lines.append(t("OpenClaw not installed{detail}", detail=f" ({openclaw.detail})" if openclaw.detail else ""))
    lines.append(t("Claude: {state}", state=_login_word(found.claude)))
    lines.append(t("Codex: {state}", state=_login_word(found.codex)))
    lines.append(t("OpenCode Go: {state}", state=t("key present") if found.opencode_go else t("no key")))
    local = found.local
    if local.available:
        lines.append(t("Local models: Ollama {ollama}, llama.cpp {llamacpp}",
                       ollama=len(local.ollama), llamacpp=len(local.llamacpp)))
    else:
        lines.append(t("Local models: none ({detail})", detail=local.detail or t("no backend answered")))
    lines.append(t("n8n: {state}", state=t("active") if found.n8n else t("not active")))
    return lines


def _login_word(login: Any) -> str:
    """One login state in words a person can act on."""
    if not login.binary:
        return t("not found")
    if login.login is True:
        return t("logged in")
    if login.login is False:
        return t("installed, not logged in")
    return t("installed, login not verifiable")


def render_plan(plan: Plan) -> str:
    """The human view: what was found, who does what, what changes, what stays."""
    lines = [t("OpenClaw plan for {path}", path=plan.path), ""]
    lines.extend(f"  {line}" for line in _detected_lines(plan))
    lines.append("")

    models = agent_models(plan.detected, plan.choices)
    lines.append(t("Agents:"))
    for agent_id, display in DISPLAY_NAMES.items():
        if agent_id in models:
            lines.append(f"  {display}: {models[agent_id]}")
    for note in _agent_notes(models, plan.detected, plan.choices):
        lines.append(f"  ! {note}")
    lines.append("")

    if plan.current is None and plan.current_error:
        lines.append(t("The configuration at {path} cannot be read: {error}", path=plan.path, error=plan.current_error))
    elif plan.current is None:
        lines.append(t("No configuration at {path} yet: everything below would be created.", path=plan.path))
    else:
        lines.append(t("Configuration in force read from {path}.", path=plan.path))
    lines.append(t("Sections this setup would change:"))
    lines.append(t("  added: {items}", items=", ".join(plan.added) or t("(none)")))
    lines.append(t("  changed: {items}", items=", ".join(plan.changed) or t("(none)")))
    if plan.kept:
        lines.append(t("  yours, left untouched: {items}", items=", ".join(plan.kept)))
    if plan.dropped:
        lines.append(t("  no longer proposed, left in place: {items}", items=", ".join(plan.dropped)))
    if plan.notes:
        lines.append("")
        lines.extend(f"  - {note}" for note in plan.notes)
    lines.append("")
    lines.append(t("Nothing was written. 'nexgen openclaw setup' (B2) applies this plan."))
    return "\n".join(lines)
