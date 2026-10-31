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
from nexgen_core.openclaw.claude_cli import ANTHROPIC, claude_cli_agents, is_claude_cli

if TYPE_CHECKING:
    from nexgen_core.openclaw.setup_steps import Setup

_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_SIMPLE_VAR_PATTERN = re.compile(r"(?<!\\)\$([A-Za-z_][A-Za-z0-9_]*)")

#: The one provider key whose mere presence in the gateway's environment is a fault:
#: OpenClaw's live catalogue sends it to `GET /v1/models` (`_retired_keys` argues it).
ANTHROPIC_API_KEY = "ANTHROPIC_API_KEY"

_KNOWN_MODEL_ENV_VARS = {
    "anthropic": [ANTHROPIC_API_KEY],
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
    """Inspects OpenClaw config and MCP servers to find all cited env vars.

    A provider's key is a reference the agent's model makes. It is not, however,
    the reference a `claude-cli` agent makes: that agent signs in through Claude
    Code, and `ANTHROPIC_API_KEY` in the gateway's environment only makes OpenClaw's
    live catalogue call `GET /v1/models` with a key that may not be allowed to.
    An explicit `${ANTHROPIC_API_KEY}` anywhere in the file is collected above and
    still counts: that is a reference somebody wrote on purpose.
    """
    found: set[str] = set()
    _extract_vars_from_val(config, found)

    agents = config.get("agents", {}).get("entries", {}) if isinstance(config.get("agents"), dict) else {}
    for agent_id, entry in agents.items():
        if isinstance(entry, dict):
            model = str(entry.get("model") or "").lower()
            for provider, vars_list in _KNOWN_MODEL_ENV_VARS.items():
                if provider not in model:
                    continue
                if provider == ANTHROPIC and is_claude_cli(config, str(agent_id)):
                    continue
                found.update(vars_list)
    return found


#: Variables every process already has. A server entry may cite them (the bridge
#: does, `${PATH}`), but copying their setup-time value into `.env` would freeze
#: them for every later start of the gateway and of the units that load the file.
PROCESS_VARS = frozenset({"PATH", "HOME", "USERPROFILE", "APPDATA", "SYSTEMROOT", "SystemRoot"})


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
    return found - PROCESS_VARS


def _format_env_line(key: str, value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'{key}="{escaped}"'


def _retired_keys(config: dict[str, Any], referenced: set[str]) -> set[str]:
    """The keys the gateway must not carry even though nothing asks for them.

    `ANTHROPIC_API_KEY` is the exception, and it is narrow on purpose. It belongs
    here when at least one agent runs on `claude-cli` and no reference is left for
    it: the key is not what runs that agent's turn, but while it sits in the
    gateway's environment OpenClaw's live discovery calls `GET /v1/models` with it,
    a workspace-scoped key answers 400, every model is reported unavailable, and
    the apps that read `models.list` say they cannot update the models on each
    open. Nothing is lost: the value stays in the engine's secret store, which is
    where the setup reads it from the next time something names it.
    """
    if not claude_cli_agents(config):
        return set()
    return {ANTHROPIC_API_KEY} - referenced


def retired_environment_keys(config: dict[str, Any], config_path: Path) -> set[str]:
    """The keys the setup takes out of the gateway's environment, given the file in force.

    The doctor asks the same question as the writer, so it asks it here rather than
    deciding for itself what a reference is.
    """
    return _retired_keys(config, extract_referenced_env_vars(config) | extract_mounted_mcp_env_vars(config_path))


def _align_values(
    content: str, values: Mapping[str, str], retired: set[str] | None = None,
) -> tuple[str, list[str], list[str], list[str]]:
    """The file with every named key at its current value, and the retired ones gone.

    Returns the new content, the keys that were rewritten, the keys that were added,
    and the keys that were removed. A line the engine does not know is left exactly
    as it is: this file also carries keys a person put there by hand, and they are
    not the engine's. `retired` is the exception the caller argues for, one key at
    a time: those lines go even though no value replaces them.
    """
    dropped = retired or set()
    rewritten: list[str] = []
    removed: list[str] = []
    present: set[str] = set()
    out: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        key = stripped.split("=", 1)[0].strip() if "=" in stripped and not stripped.startswith("#") else ""
        if key and key in dropped:
            removed.append(key)
        elif key and key in values:
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
    body = "\n".join(out).rstrip()
    return (body + "\n" if body else ""), rewritten, added, removed


def write_gateway_service_env(
    setup: Setup,
    config: dict[str, Any],
) -> Path:
    """Regenerates $OPENCLAW_STATE_DIR/.env from the values in force; never logs them.

    The keys this file carries are references the configuration and the mounted MCP
    servers make. They are regenerated every run, not filled in once: a token rotated
    in the engine's secret store used to stay alive here at its old value, and a second
    copy of a secret is exactly what the store exists to prevent. Keys the engine does
    not reference are the person's and stay untouched, except one: a key a
    `claude-cli` agent does not read is taken out, because it breaks the model
    catalogue (`_retired_keys` argues it).
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

    content, rewritten, added, removed = _align_values(existing_content, active_vars, _retired_keys(config, referenced))
    if content != existing_content or not env_file.is_file():
        atomic_write_text(env_file, content, preserve_mode=False)

    if rewritten or added:
        setup.out(t("OpenClaw environment regenerated at {path} ({rewritten} updated, {added} added).",
                    path=env_file, rewritten=len(rewritten), added=len(added)))
    elif not removed:
        setup.out(t("OpenClaw environment at {path} already carries the current values.", path=env_file))
    for key in removed:
        setup.out(t("Took {key} out of {path}: no agent reads it, and the apps cannot update the models while it "
                    "is there. The value stays in the engine's secret store.", key=key, path=env_file))
    return env_file
