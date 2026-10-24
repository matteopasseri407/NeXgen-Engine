"""Squad as data for OpenClaw agents, models, bindings, and node execution.

One job: parse squad configuration from vault squad.yaml or squad.example.yaml,
apply squad data to OpenClaw config (agents, models, bindings, tools), provide
per-agent SOUL.md definitions, and register model provider API keys via stdin.
"""
from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from nexgen_core.i18n import t
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


def get_squad_souls(squad: dict[str, Any] | None) -> dict[str, str]:
    """Returns agent_id -> SOUL.md content only for agents that declare one."""
    if not squad:
        return {}
    agents = squad.get("agents")
    if not isinstance(agents, dict):
        return {}
    souls: dict[str, str] = {}
    for agent_id, spec in agents.items():
        if isinstance(spec, dict) and "soul" in spec:
            soul_val = spec["soul"]
            if isinstance(soul_val, str) and soul_val.strip():
                souls[str(agent_id)] = soul_val.strip() + "\n"
            elif soul_val is True:
                name = spec.get("name") or agent_id
                souls[str(agent_id)] = f"# {name}\n\nOperating identity and values.\n"
    return souls


def apply_squad(
    config: dict[str, Any],
    squad: dict[str, Any],
    current: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Applies squad definitions to OpenClaw config: agents, models, bindings, tools."""
    target = dict(config)
    agents_spec = squad.get("agents")
    if isinstance(agents_spec, dict):
        entries = target.setdefault("agents", {}).setdefault("entries", {})
        allowed_senders: set[str] = set()
        for agent_id, spec in agents_spec.items():
            if not isinstance(spec, dict):
                continue
            entry = entries.setdefault(agent_id, {})
            name = spec.get("name") or agent_id
            entry["name"] = name
            entry["identity"] = {"name": name}
            if "model" in spec:
                entry["model"] = spec["model"]
            if "workspace" in spec:
                entry["workspace"] = spec["workspace"]
            if "thinkingDefault" in spec:
                entry["thinkingDefault"] = spec["thinkingDefault"]
            if "tools" in spec and isinstance(spec["tools"], dict):
                agent_tools = entry.setdefault("tools", {})
                if "exec" in spec["tools"]:
                    agent_tools["exec"] = spec["tools"]["exec"]
                if "agentToAgent" in spec["tools"]:
                    agent_tools["agentToAgent"] = spec["tools"]["agentToAgent"]
                    allowed_senders.add(agent_id)
                    for target_id in spec["tools"]["agentToAgent"].get("send", []):
                        allowed_senders.add(str(target_id))

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

    return target


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
