"""Global environment file for OpenClaw gateway.

One job: calculate variables referenced in OpenClaw config and the MCP servers
mounted in the file in force, and update $OPENCLAW_STATE_DIR/.env (mode 0600),
preserving user-defined keys and never logging secret values.
"""
from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nexgen_core.files import atomic_write_text
from nexgen_core.i18n import t
from nexgen_core.mcp_render import load_json_object

if TYPE_CHECKING:
    from nexgen_core.openclaw.setup_steps import Setup

_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_SIMPLE_VAR_PATTERN = re.compile(r"(?<!\\)\$([A-Za-z_][A-Za-z0-9_]*)")

_KNOWN_MODEL_ENV_VARS = {
    "anthropic": ["ANTHROPIC_API_KEY"],
    "openai": ["OPENAI_API_KEY"],
    "deepseek": ["DEEPSEEK_API_KEY"],
    "opencode": ["OPENCODE_GO_KEY"],
    "google": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
    "brave": ["BRAVE_API_KEY"],
}


def _extract_vars_from_val(val: Any, found: set[str]) -> None:
    if isinstance(val, str):
        for m in _VAR_PATTERN.findall(val):
            found.add(m)
        for m in _SIMPLE_VAR_PATTERN.findall(val):
            found.add(m)
    elif isinstance(val, dict):
        for k, v in val.items():
            if str(k).lower() == "env" and isinstance(v, dict):
                for env_k in v.keys():
                    if isinstance(env_k, str):
                        found.add(env_k)
            _extract_vars_from_val(v, found)
    elif isinstance(val, (list, tuple, set)):
        for item in val:
            _extract_vars_from_val(item, found)


def extract_referenced_env_vars(config: dict[str, Any]) -> set[str]:
    """Inspects OpenClaw config and MCP servers to find all cited env vars."""
    found: set[str] = set()
    _extract_vars_from_val(config, found)

    agents = config.get("agents", {}).get("entries", {}) if isinstance(config.get("agents"), dict) else {}
    for entry in agents.values():
        if isinstance(entry, dict):
            model = str(entry.get("model") or "").lower()
            for provider, vars_list in _KNOWN_MODEL_ENV_VARS.items():
                if provider in model:
                    found.update(vars_list)
    return found


def extract_mounted_mcp_env_vars(config_path: Path) -> set[str]:
    """The variables cited by the MCP servers mounted in the file in force.

    The server table is written into `openclaw.json` by `nexgen mcp render`, not
    by the setup, so it never reaches the proposal this writer is handed. A stdio
    server names its variables in its arguments and in its `env` block (the
    bridge scripts included); one missing from the gateway's environment makes
    that server start with an empty value, or not start at all.
    """
    try:
        data = load_json_object(config_path)
    except ValueError:  # nexgen: allow reason="a config that cannot be read adds no server variable; the write step names the file"
        return set()
    mcp = data.get("mcp") if isinstance(data, dict) else None
    servers = mcp.get("servers") if isinstance(mcp, dict) else None
    found: set[str] = set()
    _extract_vars_from_val(servers, found)
    return found


def _format_env_line(key: str, value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'{key}="{escaped}"'


def _align_values(content: str, values: Mapping[str, str]) -> tuple[str, list[str], list[str]]:
    """The file with every named key at its current value.

    Returns the new content, the keys that were rewritten, and the keys that were
    added. A line the engine does not know is left exactly as it is: this file also
    carries keys a person put there by hand, and they are not the engine's.
    """
    rewritten: list[str] = []
    present: set[str] = set()
    out: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        key = stripped.split("=", 1)[0].strip() if "=" in stripped and not stripped.startswith("#") else ""
        if key and key in values:
            present.add(key)
            new_line = _format_env_line(key, values[key])
            if line != new_line:
                rewritten.append(key)
            out.append(new_line)
        else:
            out.append(line)
    added = [key for key in values if key not in present]
    for key in added:
        out.append(_format_env_line(key, values[key]))
    return "\n".join(out).rstrip() + "\n", rewritten, added


def write_gateway_service_env(
    setup: Setup,
    config: dict[str, Any],
) -> Path:
    """Regenerates $OPENCLAW_STATE_DIR/.env from the values in force; never logs them.

    The keys this file carries are references the configuration and the mounted MCP
    servers make. They are regenerated every run, not filled in once: a token rotated
    in the engine's secret store used to stay alive here at its old value, and a second
    copy of a secret is exactly what the store exists to prevent. Keys the engine does
    not reference are the person's and stay untouched.
    """
    referenced = extract_referenced_env_vars(config) | extract_mounted_mcp_env_vars(setup.config_path)
    env_source: Mapping[str, str] = setup.env or os.environ
    active_vars = {k: env_source[k] for k in sorted(referenced) if k in env_source and env_source[k]}

    env_file = setup.state_dir / ".env"
    env_file.parent.mkdir(parents=True, exist_ok=True)

    existing_content = ""
    if env_file.is_file():
        existing_content = env_file.read_text(encoding="utf-8")
    else:
        existing_content = "# OpenClaw environment file\n# Variables referenced in configuration and mounted MCP servers\n"

    content, rewritten, added = _align_values(existing_content, active_vars)
    if content != existing_content or not env_file.is_file():
        atomic_write_text(env_file, content, preserve_mode=False)

    if rewritten or added:
        setup.out(t("OpenClaw environment regenerated at {path} ({rewritten} updated, {added} added).",
                    path=env_file, rewritten=len(rewritten), added=len(added)))
    else:
        setup.out(t("OpenClaw environment at {path} already carries the current values.", path=env_file))
    return env_file
