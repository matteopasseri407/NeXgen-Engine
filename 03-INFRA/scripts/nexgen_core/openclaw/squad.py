"""Squad as data for OpenClaw agents, models, bindings, and node execution.

nexgen: one-job reason="the squad as data: its agents, models, bindings, provider keys and manuals, applied to OpenClaw from one place"
One job: parse squad configuration from vault squad.yaml or squad.example.yaml,
apply squad data to OpenClaw config (agents, models, bindings, tools, the default
agent), provide per-agent SOUL.md and AGENTS.md definitions, and register model
provider API keys via stdin.
"""
from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from nexgen_core.i18n import t
from nexgen_core.openclaw import agy_backend, agy_prereqs, channels, model_policy
from nexgen_core.openclaw.claude_cli import CLAUDE_CLI
from nexgen_core.openclaw.runtime import apply_runtime
from nexgen_core.openclaw.terms import LOCAL_AGY_BACKEND
from nexgen_core.processes import no_window_kwargs

if TYPE_CHECKING:
    from nexgen_core.openclaw.setup_steps import Setup

_PROVIDER_ENV_MAP: dict[str, tuple[str, str]] = {
    "anthropic": ("anthropic", "ANTHROPIC_API_KEY"),
    "claude": ("anthropic", "ANTHROPIC_API_KEY"),
    "openai": ("openai", "OPENAI_API_KEY"),
    "gpt": ("openai", "OPENAI_API_KEY"),
    "deepseek": ("deepseek", "DEEPSEEK_API_KEY"),
    "google": ("google", "GEMINI_API_KEY"),
    "gemini": ("google", "GEMINI_API_KEY"),
}

#: The providers that run on a local CLI binary and keep the login inside it, so an
#: agent on one of them needs no provider key in OpenClaw. `codex` is the runtime id
#: `profile._default_models` writes into `agents.defaults.models`.
_CLI_BACKENDS = (CLAUDE_CLI, "codex", LOCAL_AGY_BACKEND)

#: The providers whose login the engine wires elsewhere, from its own secret store:
#: `profile.py` allows the `opencode-go` plugin and `detect.py` reads that auth profile,
#: so pasting the Go key under `deepseek` -- which this map used to do -- would put a
#: credential in the wrong provider. The Zen `opencode` prefix rides the same road.
_PROFILE_PROVIDERS = ("opencode-go", "opencode")

#: The keys one agent may set in its `exec` block, which is the whole exec policy
#: OpenClaw reads for that agent (`agents.entries.<id>.tools.exec`). `host` and
#: `node` choose where a command runs; `security` and `ask` are the pair the squad
#: still declares, and `mode` is the one field OpenClaw reads today.
_EXEC_KEYS = ("mode", "security", "ask", "host", "node")

#: The line the engine puts on top of an `AGENTS.md` it writes from the squad.
#: A file without it belongs to the person and is never replaced: the same
#: ownership test the rest of the setup applies to a file it did not create.
INSTRUCTION_MARK = "<!-- nexgen:squad:begin"

#: The full mark, saying what happens to the file it opens.
_INSTRUCTION_MARK_LINE = (
    INSTRUCTION_MARK + ": written from squad.yaml by 'nexgen openclaw setup', and rewritten on the next run -->"
)


def find_squad_file(vault_data: Path | None = None, home: Path | None = None) -> Path | None:
    """Finds the user's squad.yaml in Vault or config directory."""
    candidates: list[Path] = []
    if vault_data:
        candidates.extend([
            vault_data / "squad.yaml",
            vault_data / "03-INFRA" / "agent-universal-layer" / "squad.yaml",
            vault_data / "03-INFRA" / "squad.yaml",
        ])
    if home:
        candidates.extend([
            home / ".config" / "nexgen" / "squad.yaml",
            home / ".openclaw" / "squad.yaml",
        ])
    for path in candidates:
        if path.is_file():
            return path
    return None


def load_squad(path: Path | None) -> dict[str, Any] | None:
    """Loads and validates squad YAML data."""
    if not path or not path.is_file():
        return None
    try:
        content = yaml.safe_load(path.read_text(encoding="utf-8"))
        return content if isinstance(content, dict) else None
    except Exception:  # nexgen: allow reason="corrupt squad file is treated as absent rather than crashing setup"
        return None


#: The YAML header a personal file uses for its own metadata (its status, its name).
#: `SOUL.md` is the body of that file: the header is the engine's bookkeeping, not
#: something an agent should read about itself in its own identity.
_SOUL_HEADER = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.DOTALL)


def read_soul_source(path: Path) -> str | None:
    """The body of a personal file, without its YAML header, or None when it cannot be read."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:  # nexgen: allow reason="a source that cannot be read generates nothing, so the workspace keeps the file it already has instead of an empty identity"
        return None
    body = _SOUL_HEADER.sub("", text, count=1).strip()
    return body + "\n" if body else None


def get_squad_souls(squad: dict[str, Any] | None, squad_file: Path | None = None) -> dict[str, str]:
    """Returns agent_id -> SOUL.md content, generated from its one source.

    An agent may name a file (`soul_from`, relative to the squad file) instead of
    carrying its identity inline: that is how `agent-self.md`, the canonical home of
    the name and the character, reaches OpenClaw without being copied into a second
    file somebody can edit. The inline `soul` stays for agents that have no file of
    their own.
    """
    if not squad:
        return {}
    agents = squad.get("agents")
    if not isinstance(agents, dict):
        return {}
    souls: dict[str, str] = {}
    for agent_id, spec in agents.items():
        if not isinstance(spec, dict):
            continue
        reference = spec.get("soul_from")
        if isinstance(reference, str) and reference.strip() and squad_file is not None:
            body = read_soul_source(Path(squad_file).parent / reference.strip())
            if body:
                souls[str(agent_id)] = body
                continue
        if "soul" in spec:
            soul_val = spec["soul"]
            if isinstance(soul_val, str) and soul_val.strip():
                souls[str(agent_id)] = soul_val.strip() + "\n"
            elif soul_val is True:
                name = spec.get("name") or agent_id
                souls[str(agent_id)] = f"# {name}\n\nOperating identity and values.\n"
    return souls


def _default_reference(squad: dict[str, Any] | None) -> str | None:
    """The default agent the squad names: `default: <id>`, or the first marked entry.

    The mark is `default: true` on an agent, which is the shape a squad copied from
    an older OpenClaw roster already carries. It is read, never written back: the
    engine writes the modern `agents.defaults.systemAgent.agentId` instead, because
    OpenClaw refuses the legacy marker beside `agents.ownership: "explicit"`.
    """
    if not squad:
        return None
    declared = squad.get("default")
    if isinstance(declared, str) and declared.strip():
        return declared.strip()
    agents = squad.get("agents")
    if isinstance(agents, dict):
        for agent_id, spec in agents.items():
            if isinstance(spec, dict) and spec.get("default") is True:
                return str(agent_id)
    return None


def default_agent(squad: dict[str, Any] | None) -> str | None:
    """The agent the squad declares as this installation's default, if it declares one.

    Only a declared agent can be the default: naming an id the squad does not
    define would write an owner OpenClaw cannot resolve, which is the refusal this
    field exists to prevent.
    """
    name = _default_reference(squad)
    agents = squad.get("agents") if isinstance(squad, dict) else None
    return name if name and isinstance(agents, dict) and name in agents else None


def unresolved_default(squad: dict[str, Any] | None) -> str | None:
    """The default the squad names but does not declare, so the plan can name the typo."""
    name = _default_reference(squad)
    return None if default_agent(squad) or not name else name


def _exec_policy(spec: Mapping[str, Any]) -> dict[str, str]:
    """The exec policy one agent declares, keeping only the keys OpenClaw reads."""
    raw = spec.get("exec")
    if not isinstance(raw, dict):
        return {}
    return {
        key: str(raw[key]).strip()
        for key in _EXEC_KEYS
        if isinstance(raw.get(key), str) and str(raw[key]).strip()
    }


def exec_mode(policy: Mapping[str, Any]) -> str | None:
    """OpenClaw's normalized exec policy, from the keys the squad may declare.

    `mode` replaced `security` + `ask` in the gateway config, which is the shape
    its own `doctor --fix` migrates to: `ask=always` is a human-reviewed miss,
    `ask=on-miss` a classifier-reviewed one, and with `ask=off` the security level
    is the whole policy. A policy that names `mode` already is used as it stands.
    Without either a mode or a security level there is nothing honest to derive,
    so the answer is None and the declared keys are left alone.
    """
    declared = str(policy.get("mode") or "").strip()
    if declared:
        return declared
    ask = str(policy.get("ask") or "").strip()
    if ask == "always":
        return "ask"
    if ask == "on-miss":
        return "auto"
    security = str(policy.get("security") or "").strip()
    return security or None


def _normalized_exec(policy: Mapping[str, Any]) -> dict[str, Any]:
    """One `tools.exec` block in the shape the gateway reads today.

    Everything the squad declared survives except the older `security`/`ask`
    pair, which is expressed as `mode` when it can be. Unknown keys are copied
    as declared: they are OpenClaw's, not this engine's, to judge.
    """
    block = {key: value for key, value in policy.items() if key not in ("security", "ask")}
    mode = exec_mode(policy)
    if mode:
        block["mode"] = mode
    else:
        for key in ("security", "ask"):
            if key in policy:
                block[key] = policy[key]
    return block


def _declared_path(value: Any, base: Path) -> Path | None:
    """A path the squad declares, resolved the way the file means it: beside itself."""
    if not isinstance(value, str) or not value.strip():
        return None
    path = Path(value.strip()).expanduser()
    return path if path.is_absolute() else base / path


def _read_instruction(path: Path | None) -> str | None:
    """The text of one declared instruction file, or None when it cannot be read."""
    if path is None or not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8-sig").strip() or None
    except OSError:  # nexgen: allow reason="an unreadable instruction file is named by the setup step, never a crash"
        return None


def _with_mark(text: str) -> str:
    """The instruction text with the ownership mark on top, so a later run may replace it."""
    return f"{_INSTRUCTION_MARK_LINE}\n{text.strip()}\n"


def get_squad_instructions(
    squad: dict[str, Any] | None,
    squad_file: Path | None,
) -> tuple[dict[str, str], tuple[str, ...]]:
    """Returns agent_id -> AGENTS.md text, plus the declared files that cannot be read.

    `agents_md` is one agent's own instructions; `common_md`, declared at the root,
    is appended to every agent that has either. Paths are relative to the squad
    file, so the two travel together. The text carries the engine's mark because
    the setup replaces only a file that has one.
    """
    if not squad or squad_file is None:
        return {}, ()
    agents = squad.get("agents")
    if not isinstance(agents, dict):
        return {}, ()
    base = squad_file.parent
    common_path = _declared_path(squad.get("common_md"), base)
    common = _read_instruction(common_path)
    unreadable: list[str] = []
    if common_path is not None and common is None:
        unreadable.append(str(common_path))
    texts: dict[str, str] = {}
    for agent_id, spec in agents.items():
        if not isinstance(spec, dict):
            continue
        role_path = _declared_path(spec.get("agents_md"), base)
        role = _read_instruction(role_path)
        if role_path is not None and role is None:
            unreadable.append(str(role_path))
        parts = [part for part in (role, common) if part]
        if parts:
            texts[str(agent_id)] = _with_mark("\n\n".join(parts))
    return texts, tuple(unreadable)


def _apply_agent_entry(entry: dict[str, Any], spec: Mapping[str, Any], agent_id: Any) -> None:
    """One agent's own fields on its entry.

    A `tools.agentToAgent.send` the squad declares is that agent's own narrowing
    and is written as declared: the engine never adds a global `allow` list, whose
    absence OpenClaw reads as every pair allowed.
    """
    name = spec.get("name") or agent_id
    entry["name"] = name
    entry["identity"] = {"name": name}
    if "model" in spec:
        entry["model"] = spec["model"]
    if "workspace" in spec:
        entry["workspace"] = spec["workspace"]
    if "thinkingDefault" in spec:
        entry["thinkingDefault"] = spec["thinkingDefault"]
    declared_tools = spec.get("tools") if isinstance(spec.get("tools"), dict) else {}
    if "agentToAgent" in declared_tools:
        entry.setdefault("tools", {})["agentToAgent"] = declared_tools["agentToAgent"]
    # `exec` at the agent's own level is the documented place for its policy;
    # the older `tools.exec` shape still works and the newer field wins per key.
    policy = _exec_policy(spec)
    nested = declared_tools.get("exec")
    if policy or isinstance(nested, dict):
        merged = dict(nested) if isinstance(nested, dict) else {}
        merged.update(policy)
        entry.setdefault("tools", {})["exec"] = _normalized_exec(merged)
    elif "exec" in declared_tools:
        # A shape OpenClaw will refuse is copied as declared, not dropped in silence.
        entry.setdefault("tools", {})["exec"] = nested
    deny = _tools_deny(spec)
    if deny is not None:
        # Additive: a pattern already on the entry stays, the squad only adds.
        tools = entry.setdefault("tools", {})
        existing = tools.get("deny") if isinstance(tools.get("deny"), list) else []
        tools["deny"] = list(dict.fromkeys([*existing, *deny]))


def _tools_deny(spec: Mapping[str, Any]) -> list[str] | None:
    """The agent's `tools_deny` glob patterns, or None when the field is absent.

    OpenClaw names an MCP tool `server__tool`, so `server__*` hides a whole
    server from one agent. A field that is not a list is ignored, not guessed.
    """
    declared = spec.get("tools_deny")
    if not isinstance(declared, list):
        return None
    return [item.strip() for item in declared if isinstance(item, str) and item.strip()]


def get_squad_skills(squad: dict[str, Any] | None) -> dict[str, list[str]]:
    """The skill names each agent declares under `skills`, by agent id.

    An agent without the field is absent from the result, so its workspace
    `skills/` folder is left as it is.
    """
    found: dict[str, list[str]] = {}
    agents = (squad or {}).get("agents")
    if not isinstance(agents, dict):
        return found
    for agent_id, spec in agents.items():
        if isinstance(spec, dict) and isinstance(spec.get("skills"), list):
            names = [item.strip() for item in spec["skills"] if isinstance(item, str) and item.strip()]
            found[str(agent_id)] = list(dict.fromkeys(names))
    return found


def _declared_models(squad: dict[str, Any]) -> list[str]:
    """Every model the squad names, as data: each agent's default and the `models` table."""
    refs: list[str] = []
    agents = squad.get("agents")
    if isinstance(agents, dict):
        for spec in agents.values():
            if isinstance(spec, dict) and isinstance(spec.get("model"), str):
                refs.append(spec["model"])
    declared = squad.get("models")
    if isinstance(declared, dict):
        refs.extend(str(key) for key in declared)
    return refs


def _apply_model_entries(config: dict[str, Any], declared: Mapping[str, Any]) -> None:
    """The squad's `models` table, merged into `agents.defaults.models`.

    That record is `provider/model` -> entry, and it is where OpenClaw reads a
    per-model setting such as the `agentRuntime: {id: "claude-cli"}` pin
    `squad.example.yaml` declares. The root `models` object is strict -- only
    `mode`, `providers` and `catalogRefresh` -- so an `entries` key there makes
    OpenClaw refuse the whole file ("models: Unrecognized key: entries"), which is
    what an earlier engine wrote. An entry already on the target keeps every key
    the squad does not set; the squad's own keys win over it.
    """
    agents_block = config.setdefault("agents", {})
    defaults = agents_block.setdefault("defaults", {})
    models = defaults.get("models") if isinstance(defaults.get("models"), dict) else {}
    for ref, entry in declared.items():
        existing = models.get(ref) if isinstance(models.get(ref), dict) else {}
        models[str(ref)] = {**existing, **(entry if isinstance(entry, dict) else {})}
    defaults["models"] = models


def apply_squad(
    config: dict[str, Any],
    squad: dict[str, Any],
    current: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Applies squad definitions to OpenClaw config: agents, models, bindings, tools."""
    target = dict(config)
    agents_spec = squad.get("agents")
    if isinstance(agents_spec, dict):
        agents_block = target.setdefault("agents", {})
        entries = agents_block.setdefault("entries", {})
        for agent_id, spec in agents_spec.items():
            if isinstance(spec, dict):
                _apply_agent_entry(entries.setdefault(agent_id, {}), spec, agent_id)

        default_id = default_agent(squad)
        if default_id:
            # Without this owner, a roster with more than one explicit agent answers
            # the main session with "no explicit owner": Talk, the main session and
            # the dreaming cron all need one agent named here.
            defaults = agents_block.setdefault("defaults", {})
            defaults.setdefault("systemAgent", {})["agentId"] = default_id

        # Removing an agent from the squad removes it here too, but only when the
        # squad says so: the engine is additive by default, and a person who added
        # an agent by hand must not lose it to a sync.
        if squad.get("prune_agents") is True and isinstance(agents_spec, dict):
            declared = set(agents_spec)
            for agent_id in [key for key in entries if key not in declared]:
                entries.pop(agent_id, None)

    if isinstance(squad.get("models"), dict):
        _apply_model_entries(target, squad["models"])

    if "bindings" in squad and isinstance(squad["bindings"], list):
        target = channels.with_bindings(target, squad["bindings"])

    # The runtime block decides how many chats each agent gets and how long a
    # manual fork lives. It is policy, so it travels with the squad instead of
    # being re-done by hand on every machine.
    runtime_spec = squad.get("runtime")
    if isinstance(runtime_spec, dict):
        apply_runtime(target, runtime_spec)

    # A model the squad names is that agent's default, and it must be selectable from the
    # picker of every other agent too: registering it does not change anyone's `model`.
    applied = model_policy.with_declared(target, _declared_models(squad))
    return agy_backend.with_backend(applied, current) if agy_prereqs.requested(squad) else applied


def _cli_backend(model: str) -> str:
    """The local CLI a model ref runs on, or "" when OpenClaw may hold a key for it.

    Read from the prefix before the slash, never by substring: `google-antigravity-cli`
    is a backend of its own, not a `google` model, and `terms.py` reads it the same way.
    """
    low = str(model or "").strip().lower()
    prefix, sep, _ = low.partition("/")
    name = prefix if sep else low
    return name if name in _CLI_BACKENDS else ""


def _infer_provider(model: str) -> tuple[str, str] | None:
    """The provider a model ref names, and the environment variable its key lives in.

    The provider is the prefix before the slash, as `terms.py` reads it. A substring
    test reads `google-antigravity-cli/gemini-3.8-flash` as `google` and, with
    `GEMINI_API_KEY` in the environment, pastes an AI Studio key for an agent that runs
    on the local `agy` CLI: a credential on a road `docs/openclaw.md` declares closed.
    A ref with no prefix is still read by name, which is how `gemini-2.5-flash` names
    Google.
    """
    if _cli_backend(model):
        return None
    low = str(model or "").strip().lower()
    prefix, sep, _ = low.partition("/")
    if sep:
        return None if prefix in _PROFILE_PROVIDERS else _PROVIDER_ENV_MAP.get(prefix)
    for name, pair in _PROVIDER_ENV_MAP.items():
        if name in low:
            return pair
    return None


def register_model_keys(
    setup: Setup,
    config: dict[str, Any],
    openclaw_cmd: str | None = None,
) -> int:
    """Registers provider API keys for squad agents via OpenClaw CLI stdin."""
    bin_cmd = openclaw_cmd or setup.which("openclaw") or "openclaw"
    agents = config.get("agents", {}).get("entries", {}) if isinstance(config.get("agents"), dict) else {}
    registered_count = 0

    env_source: Mapping[str, str] = setup.env or os.environ
    for agent_id, entry in agents.items():
        if not isinstance(entry, dict):
            continue
        model = str(entry.get("model") or "")
        backend = _cli_backend(model)
        if backend:
            # Said once per agent, so a person who expected a key registered reads why
            # none was: the login stays in the CLI that already has it.
            setup.out(t("No key is registered for agent '{agent}': its model runs on the local {backend} CLI, which keeps its own login.",
                        agent=agent_id, backend=backend))
            continue
        match = _infer_provider(model)
        if not match:
            continue
        provider, env_var = match
        api_key = env_source.get(env_var)
        if not api_key:
            continue

        cmd = [bin_cmd, "models", "auth", "paste-api-key", "--agent", str(agent_id), "--provider", provider]
        if setup.dry_run:
            setup.out(t("Would register {provider} API key for agent '{agent}' via stdin.",
                        provider=provider, agent=agent_id))
            registered_count += 1
            continue

        try:
            res = subprocess.run(
                cmd,
                input=f"{api_key}\n",
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                env=setup.env,
                check=False,
                **no_window_kwargs(),
            )
            if res.returncode == 0:
                setup.out(t("Registered {provider} API key for agent '{agent}' via OpenClaw auth.",
                            provider=provider, agent=agent_id))
                registered_count += 1
            else:
                setup.out(t("Could not register {provider} key for agent '{agent}' (exit {code}).",
                            provider=provider, agent=agent_id, code=res.returncode))
        except Exception as exc:  # nexgen: allow reason="models auth command invocation failure reports error without crashing setup"
            setup.out(t("Models auth failed for agent '{agent}': {error}", agent=agent_id, error=exc))

    return registered_count
