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

from nexgen_core.i18n import detect_language, t
from nexgen_core.openclaw.config_file import read_config
from nexgen_core.openclaw.detect import Detection, detect
from nexgen_core.openclaw.profile import (
    CLAUDE_MODEL,
    CODEX_MODEL,
    OWNED_SECTIONS,
    PENDING_TOKEN,
    Choices,
    agent_models,
    build_config,
)
from nexgen_core.paths import openclaw_config
from nexgen_core.runtimes.openclaw import PLUGIN_ID

DISPLAY_NAMES = {"main": "assistente", "capo": "capo", "automazioni": "automazioni", "sviluppo": "sviluppo"}


def _with_engine_plugin(config: dict[str, Any], deposited: bool) -> dict[str, Any]:
    """The engine's guardrail plugin, enabled only when its folder is on disk.

    Enabling a plugin that is not there makes OpenClaw fail to start; the
    plugin is deposited by the runtime adapter, which is the only thing that
    knows how. The plan and the setup therefore share this one decision, so
    what the plan prints is what the setup writes.
    """
    if not deposited:
        return config
    plugins = dict(config.get("plugins") or {})
    allow = list(plugins.get("allow") or [])
    if PLUGIN_ID not in allow:
        allow.append(PLUGIN_ID)
    entries = dict(plugins.get("entries") or {})
    entries[PLUGIN_ID] = {**dict(entries.get(PLUGIN_ID) or {}), "enabled": True}
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


def build_plan(
    detected: Detection | None = None,
    choices: Choices | None = None,
    *,
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
    engine_root: Path | None = None,
    vault_data: Path | None = None,
    plugin_deposited: bool | None = None,
) -> Plan:
    """The proposal for this machine, diffed against the file in force."""
    snapshot = detected if detected is not None else detect(
        home=home, env=env, engine_root=engine_root, vault_data=vault_data,
    )
    chosen = choices or Choices(lang=detect_language())
    path = openclaw_config(home, env)
    current, error = read_config(path)
    config = build_config(snapshot, chosen, current)
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
    kept = tuple(sorted(key for key in (current or {}) if key not in owned))
    dropped = tuple(sorted(
        key for key in current_flat
        if key.split(".", 1)[0] in owned and key not in proposed_flat
    ))
    return Plan(
        detected=snapshot, choices=chosen, config=config, path=path,
        current=current, current_error=error,
        added=added, changed=changed, kept=kept, dropped=dropped,
        notes=tuple(_build_notes(config, snapshot, chosen)),
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
