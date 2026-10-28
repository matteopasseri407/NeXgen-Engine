"""`nexgen openclaw plan`: what would change, and what stays the person's.

nexgen: one-job reason="build the configuration a setup would write and say what changes, without writing anything; every decision it needs belongs to the modules it calls"
The plan is built from the same pure function B2 will use to write, and from a
read-only view of the file in force, so what it prints is what a setup would
produce. It writes nothing: the promise is not a convention here, it is what
the code can do — there is no write call in this module at all.
"""
from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from nexgen_core import quota_policy
from nexgen_core.i18n import detect_language, t
from nexgen_core.openclaw import companion as app_gateway
from nexgen_core.openclaw import claude_loopback, observation
from nexgen_core.openclaw.claude_cli import claude_cli_agents
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
from nexgen_core.openclaw.setup_write import merged_document
from nexgen_core.openclaw.skills_policy import with_skill_policy
from nexgen_core.openclaw.squad import apply_squad, find_squad_file, load_squad, unresolved_default
from nexgen_core.paths import openclaw_config, resolve_home, resolve_vault_data
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
            "agents": planned_agents(self.config),
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


def planned_agents(config: dict[str, Any]) -> dict[str, str]:
    """The agents the proposed configuration creates, with the model each one runs.

    Read from the configuration and not from the engine's own roster, because a
    `squad.yaml` adds and renames agents after that roster is applied: the plan has to
    name the team a setup would write, not the one it started from. An agent with no
    model of its own runs the inherited default, which is named here too.
    """
    agents = config.get("agents") if isinstance(config.get("agents"), dict) else {}
    entries = agents.get("entries") if isinstance(agents.get("entries"), dict) else {}
    defaults = agents.get("defaults") if isinstance(agents.get("defaults"), dict) else {}
    inherited = defaults.get("model")
    inherited = inherited.get("primary") if isinstance(inherited, dict) else inherited
    planned: dict[str, str] = {}
    for agent_id, entry in entries.items():
        model = entry.get("model") if isinstance(entry, dict) else None
        planned[str(agent_id)] = str(model or inherited or "")
    return planned


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    """Every leaf of a nested dict, addressed as a dotted path."""
    if isinstance(value, dict):
        leaves: dict[str, Any] = {}
        for key, item in value.items():
            leaves.update(_flatten(item, f"{prefix}.{key}" if prefix else str(key)))
        return leaves
    return {prefix: value}


def _agent_notes(
    models: dict[str, str], detected: Detection, choices: Choices | None = None,
    planned: Collection[str] = (),
) -> list[str]:
    """Which agents the plan creates, and why one is missing when it is.

    `models` is the roster the engine builds before a squad is applied, and `planned`
    is what the proposal really carries: an agent the squad declared is not missing,
    and no note may say it is.
    """
    notes: list[str] = []
    if "main" not in models and "main" not in planned:
        notes.append(t("The assistant is not created: no model is available for it."))
    if "capo" not in models and "capo" not in planned:
        notes.append(t("The code agent is not created: no model is available for it."))
    if detected.n8n and "automazioni" not in models and "automazioni" not in planned:
        notes.append(t("The automation agent is not created: no model is available for it."))
    if not detected.n8n:
        notes.append(t("The automation agent is not created: the n8n module is not active on this machine."))
    note = _sviluppo_note(models, detected, choices or Choices(), planned)
    if note:
        notes.append(note)
    return notes


def _sviluppo_note(
    models: dict[str, str], detected: Detection, chosen: Choices, planned: Collection[str] = (),
) -> str | None:
    """Why the OpenCode agent is missing, said in the terms that can fix it."""
    if "sviluppo" in models or "sviluppo" in planned:
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
    notes.extend(_agent_notes(models, detected, choices, planned_agents(config)))
    if "agents" not in config:
        notes.append(t("The setup writes no agents section: OpenClaw refuses one with no entry, and this "
                       "machine has no model to give an agent. Configure a model, then run the setup again."))
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


def _with_squad(
    config: dict[str, Any],
    vault_data: Path | None,
    home: Path | None,
    current: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """The proposal with the squad applied, and the squad itself for the notes."""
    squad_data = load_squad(find_squad_file(vault_data, home=home))
    if not squad_data:
        return config, None
    return apply_squad(config, squad_data, current), squad_data


def _servers_in_force(current: dict[str, Any] | None) -> set[str]:
    """The connector names the file already mounts under `mcp.servers`."""
    mcp = current.get("mcp") if isinstance(current, dict) else None
    servers = mcp.get("servers") if isinstance(mcp, dict) else None
    return {str(name) for name in servers} if isinstance(servers, dict) else set()


def _connector_names(
    home: Path,
    vault_data: Path | None,
    engine_root: Path | None,
    current: dict[str, Any] | None,
) -> list[str]:
    """Every connector this gateway carries or will carry: the ones in force, plus the ones sync mounts.

    The second half is not a convenience. A setup that denied only the connectors
    already in the file would leave the loopback open for exactly the connectors the
    next `nexgen sync` mounts, which is the state the incident happened in. The
    question is asked of the same renderer sync uses, so the two cannot disagree
    about which server is mounted for OpenClaw.
    """
    names = _servers_in_force(current)
    try:
        from nexgen_core.renderer import McpRenderer

        renderer = McpRenderer(vault_data=vault_data, engine_root=engine_root, home=home)
        names.update(renderer.load_resolved_servers("openclaw"))
    except (OSError, ValueError):  # nexgen: allow reason="a manifest this machine cannot read leaves the connectors already in force, and the deny must still cover those"
        pass
    return sorted(names)


def _with_loopback(
    config: dict[str, Any],
    current: dict[str, Any] | None,
    *,
    home: Path,
    vault_data: Path | None,
    engine_root: Path | None,
) -> dict[str, Any]:
    """A `claude-cli` agent reads its connectors from Claude Code, not from the loopback.

    The question is asked of the document the write will leave, not of the proposal:
    the merge keeps every key the proposal does not carry, so `agents.defaults.models`
    in the file -- and the agents the engine no longer declares -- can put an agent on
    `claude-cli` while the proposal says nothing about it. The doctor reads that merged
    file, so a plan that judged the proposal alone wrote its deny for the agents it
    happened to create and left the loopback open for the others, on every run.

    Nothing is asked of a machine with no such agent: a proposal whose agents run
    elsewhere keeps the tool surface it had, byte for byte.
    """
    agents = claude_cli_agents(merged_document(current, config))
    if not agents:
        return config
    return claude_loopback.apply(
        config, _connector_names(home, vault_data, engine_root, current), agents,
    )


def _squad_notes(squad_data: dict[str, Any] | None) -> list[str]:
    """What the squad asked for and the plan must not swallow: a default it never declares."""
    unknown = unresolved_default(squad_data)
    if not unknown:
        return []
    return [t("The squad names {agent} as the default agent but does not declare it: no default was written.",
              agent=unknown)]


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
    proposed, squad_data = _with_squad(
        build_config(snapshot, chosen, current, home=home_dir, platform=platform),
        # The setup command passes no vault: the squad still lives in the user's one.
        vault_data if vault_data is not None else resolve_vault_data(home), home_dir, current,
    )
    config = _with_engine_plugin(
        with_chains(with_skill_policy(proposed, vault_data, current), failover), _plugin_deposited(path, plugin_deposited),
    )
    # Last, and on purpose: the deny is decided on the document the write will leave,
    # so it sees what every pass above proposed and what the merge will keep.
    config = _with_loopback(config, current, home=home_dir, vault_data=vault_data, engine_root=engine_root)
    changes = _diff(config, current)
    notes = [
        *_wanted_notes(config, snapshot, chosen, squad_data),
        # A fallback line the engine cannot use is named here too: the plan is what a person
        # reads before the setup writes anything, so a typo in the policy must not be silent.
        *failover_notes,
        *_change_notes(config, changes, current),
    ]
    return Plan(
        detected=snapshot, choices=chosen, config=config, path=path,
        current=current, current_error=error,
        added=changes["added"], changed=changes["changed"],
        kept=changes["kept"], dropped=changes["dropped"],
        notes=tuple(notes), failover=failover,
    )


def _plugin_deposited(path: Path, asked: bool | None) -> bool:
    """Whether the engine's entrypoint is in place: a caller that looked already knows."""
    if asked is not None:
        return asked
    return (path.parent / "extensions" / PLUGIN_ID / "index.mjs").is_file()


def _diff(config: dict[str, Any], current: dict[str, Any] | None) -> dict[str, tuple[str, ...]]:
    """What a setup would add, change, leave alone, and no longer propose."""
    proposed_flat = _flatten(config)
    current_flat = _flatten(current or {})
    owned = set(OWNED_SECTIONS)
    touched = {key.split(".", 1)[0] for key in proposed_flat}
    return {
        "added": tuple(sorted(key for key in proposed_flat if key not in current_flat)),
        "changed": tuple(sorted(
            key for key, value in proposed_flat.items()
            if key in current_flat and current_flat[key] != value
        )),
        "kept": tuple(sorted(key for key in (current or {}) if key not in owned and key not in touched)),
        "dropped": tuple(sorted(
            key for key in current_flat
            if key.split(".", 1)[0] in owned and key not in proposed_flat
        )),
    }


def _wanted_notes(
    config: dict[str, Any], detected: Detection, choices: Choices, squad_data: dict[str, Any] | None,
) -> list[str]:
    """What the proposal itself asks for, and where it cannot have it."""
    return [*_build_notes(config, detected, choices), *_squad_notes(squad_data)]


def _exec_mode_note(current: dict[str, Any] | None) -> str | None:
    """The exec mode the engine leaves alone, named so the plan is not silent about it.

    The proposal carries `mode` only where the file has none, so a file that
    already decides never appears in `changed`. A person who wrote `ask` must
    still read that it stayed.
    """
    tools = current.get("tools") if isinstance(current, dict) else None
    block = tools.get("exec") if isinstance(tools, dict) else None
    mode = block.get("mode") if isinstance(block, dict) else None
    if not isinstance(mode, str) or not mode.strip():
        return None
    return t("tools.exec.mode stays as you wrote it ({mode}): the engine proposes its own only where "
             "none is set.", mode=mode.strip())


def _change_notes(
    config: dict[str, Any], changes: dict[str, tuple[str, ...]], current: dict[str, Any] | None,
) -> list[str]:
    """What the change means for the machine: a restart, the channels bound, an allowlist already there."""
    notes: list[str] = []
    polling_key = f"env.vars.{observation.POLLING_VAR}"
    if polling_key in (*changes["added"], *changes["changed"]):
        notes.append(t("OpenClaw changes how it watches its configuration only when it starts: "
                       "restart the gateway once for this key to take effect."))
    for note in (_binding_note(config), _supervisor_tool_note(current), _exec_mode_note(current)):
        if note:
            notes.append(note)
    return notes


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


def _agent_label(config: dict[str, Any], agent_id: str) -> str:
    """The name a person reads for one agent: what the proposal calls it, or the engine's own word.

    A squad may rename an agent, and the plan must use the name the chat will show,
    not the identifier the file uses.
    """
    agents = config.get("agents") if isinstance(config.get("agents"), dict) else {}
    entries = agents.get("entries") if isinstance(agents.get("entries"), dict) else {}
    entry = entries.get(agent_id) if isinstance(entries.get(agent_id), dict) else {}
    identity = entry.get("identity") if isinstance(entry.get("identity"), dict) else {}
    name = identity.get("name") or entry.get("name")
    return str(name or DISPLAY_NAMES.get(agent_id, agent_id))


def render_plan(plan: Plan) -> str:
    """The human view: what was found, who does what, what changes, what stays."""
    lines = [t("OpenClaw plan for {path}", path=plan.path), ""]
    lines.extend(f"  {line}" for line in _detected_lines(plan))
    lines.append("")

    lines.append(t("Agents:"))
    planned = planned_agents(plan.config)
    for agent_id, model in planned.items():
        lines.append(f"  {_agent_label(plan.config, agent_id)}: {model or t('no model declared')}")
    for note in _agent_notes(agent_models(plan.detected, plan.choices), plan.detected, plan.choices, planned):
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
