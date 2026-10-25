"""Squad as data for OpenClaw agents, models, bindings, and node execution.

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
from nexgen_core.openclaw import model_policy
from nexgen_core.processes import no_window_kwargs

if TYPE_CHECKING:
    from nexgen_core.openclaw.setup_steps import Setup

_PROVIDER_ENV_MAP: dict[str, tuple[str, str]] = {
    "anthropic": ("anthropic", "ANTHROPIC_API_KEY"),
    "claude": ("anthropic", "ANTHROPIC_API_KEY"),
    "openai": ("openai", "OPENAI_API_KEY"),
    "gpt": ("openai", "OPENAI_API_KEY"),
    "deepseek": ("deepseek", "DEEPSEEK_API_KEY"),
    "opencode-go": ("deepseek", "OPENCODE_GO_KEY"),
    "google": ("google", "GEMINI_API_KEY"),
    "gemini": ("google", "GEMINI_API_KEY"),
}

#: The keys one agent may set in its `exec` block, which is the whole exec policy
#: OpenClaw reads for that agent (`agents.entries.<id>.tools.exec`). `host` and
#: `node` choose where a command runs; `security` and `ask` decide whether it needs
#: a human approval.
_EXEC_KEYS = ("security", "ask", "host", "node")

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


def _apply_agent_entry(entry: dict[str, Any], spec: Mapping[str, Any], agent_id: Any) -> set[str]:
    """One agent's own fields on its entry; returns the ids it declares it may message."""
    name = spec.get("name") or agent_id
    entry["name"] = name
    entry["identity"] = {"name": name}
    if "model" in spec:
        entry["model"] = spec["model"]
    if "workspace" in spec:
        entry["workspace"] = spec["workspace"]
    if "thinkingDefault" in spec:
        entry["thinkingDefault"] = spec["thinkingDefault"]
    senders: set[str] = set()
    declared_tools = spec.get("tools") if isinstance(spec.get("tools"), dict) else {}
    if "agentToAgent" in declared_tools:
        entry.setdefault("tools", {})["agentToAgent"] = declared_tools["agentToAgent"]
        senders.add(agent_id)
        for target_id in declared_tools["agentToAgent"].get("send", []):
            senders.add(str(target_id))
    # `exec` at the agent's own level is the documented place for its policy;
    # the older `tools.exec` shape still works and the newer field wins per key.
    policy = _exec_policy(spec)
    nested = declared_tools.get("exec")
    if policy or isinstance(nested, dict):
        merged = dict(nested) if isinstance(nested, dict) else {}
        merged.update(policy)
        entry.setdefault("tools", {})["exec"] = merged
    elif "exec" in declared_tools:
        # A shape OpenClaw will refuse is copied as declared, not dropped in silence.
        entry.setdefault("tools", {})["exec"] = nested
    return senders


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
        allowed_senders: set[str] = set()
        for agent_id, spec in agents_spec.items():
            if isinstance(spec, dict):
                allowed_senders |= _apply_agent_entry(entries.setdefault(agent_id, {}), spec, agent_id)

        default_id = default_agent(squad)
        if default_id:
            # Without this owner, a roster with more than one explicit agent answers
            # the main session with "no explicit owner": Talk, the main session and
            # the dreaming cron all need one agent named here.
            defaults = agents_block.setdefault("defaults", {})
            defaults.setdefault("systemAgent", {})["agentId"] = default_id

        if allowed_senders:
            tools_block = target.setdefault("tools", {})
            a2a = tools_block.setdefault("agentToAgent", {})
            a2a["enabled"] = True
            current_allow = set(a2a.get("allow", []))
            a2a["allow"] = sorted(current_allow | allowed_senders)

    if "models" in squad and isinstance(squad["models"], dict):
        models_block = target.setdefault("models", {})
        models_entries = models_block.setdefault("entries", {})
        models_entries.update(squad["models"])

    if "bindings" in squad and isinstance(squad["bindings"], list):
        target["bindings"] = list(squad["bindings"])

    # A model the squad names is that agent's default, and it must be selectable from the
    # picker of every other agent too: registering it does not change anyone's `model`.
    return model_policy.with_declared(target, _declared_models(squad))


def _infer_provider(model: str) -> tuple[str, str] | None:
    low = model.lower()
    for prefix, (provider, env_var) in _PROVIDER_ENV_MAP.items():
        if prefix in low:
            return provider, env_var
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
