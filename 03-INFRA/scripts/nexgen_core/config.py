"""Config loading and validation, tolerant of the future (Forward Compatibility).

Contract invariant 8:
Code and configuration travel on different clocks: a machine that receives
configuration with new fields must NEVER stop or reject the document, but
must ignore unknown fields with a warning and apply the rest.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("nexgen.config")

#: The four runtimes the layer knows how to configure. Applies to both the
#: MCP connectors and the skill views: it's the same list, and keeping it in
#: two separate constants is how the two lists end up drifting apart.
RUNTIME_TARGETS = frozenset({"claude", "codex", "antigravity", "opencode"})
SKILL_TARGETS = RUNTIME_TARGETS
SKILL_ORIGINS = frozenset({"vault", "engine", "github", "installer", "upstream"})
SKILL_EXPOSURES = frozenset({"lazy", "eager", "manual", "core"})


class ConfigError(ValueError):
    """Blocking configuration error (e.g. malformed YAML or missing required fields)."""


def _load_yaml(path: Path, label: str) -> dict[str, Any]:
    """Loads a YAML file, verifying the root is a mapping."""
    if not path.is_file():
        raise ConfigError(f"{label} not found: {path}")
    try:
        content = path.read_text(encoding="utf-8")
        data = yaml.safe_load(content)
    except OSError as exc:
        raise ConfigError(f"Could not read {label} ({path}): {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML syntax in {label} ({path}): {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigError(f"The root of {label} ({path}) must be a map/dictionary")
    return data


def expand_placeholders(text: str, context: dict[str, str] | None = None) -> str:
    """Expands path and env-var placeholders: ${VAR}, ${VAR:-default}."""
    ctx = context or {}

    def _replace_match(match: re.Match) -> str:
        var_expr = match.group(1)
        if ":-" in var_expr:
            var_name, default_val = var_expr.split(":-", 1)
        else:
            var_name, default_val = var_expr, ""

        if var_name in ctx:
            return ctx[var_name]
        return os.environ.get(var_name, default_val)

    return re.sub(r"\$\{([^}]+)\}", _replace_match, text)


def load_mcp_manifest(path: Path) -> dict[str, Any]:
    """Loads and validates mcp/manifest.yaml tolerantly.

    ``retired_servers`` is the explicit, cross-CLI removal mechanism: a name
    listed there disappears from the returned active connectors, so every
    consumer (the renderer, dependency watch) sees it exactly once. A name
    present both among the retired and among the active servers is a manifest
    error: the active entry is skipped with a warning, and the document is
    never rejected (invariant 8).
    """
    raw = _load_yaml(path, "MCP manifest")
    servers = raw.get("servers", {})
    if not isinstance(servers, dict):
        raise ConfigError(f"{path}: 'servers' must be a map of connectors")

    retired_raw = raw.get("retired_servers", [])
    if not isinstance(retired_raw, list):
        logger.warning("'retired_servers' in %s must be a list, ignored", path)
        retired_raw = []
    retired_servers = {str(name) for name in retired_raw if str(name).strip()}

    validated_servers: dict[str, dict[str, Any]] = {}
    for name, srv in servers.items():
        if not isinstance(srv, dict):
            logger.warning("Connector '%s' in %s is invalid, entry skipped", name, path)
            continue

        # Check for essential fields
        cmd = srv.get("command") or srv.get("cmd")
        url = srv.get("url")
        if not cmd and not url:
            logger.warning("Connector '%s' specifies neither 'command' nor 'url', entry skipped", name)
            continue

        if str(name) in retired_servers:
            logger.warning(
                "Connector '%s' in %s is both active and retired: inconsistent manifest, "
                "skipping the active entry (stays retired)", name, path,
            )
            continue

        validated_servers[str(name)] = srv

    return {
        "schema_version": raw.get("schema_version", 1),
        "servers": validated_servers,
        "retired_servers": retired_servers,
        "hooks": raw.get("hooks", []),
        "raw": raw,
    }


def load_skills_manifest(path: Path) -> dict[str, Any]:
    """Loads and validates skills.manifest.yaml tolerantly."""
    raw = _load_yaml(path, "Skills manifest")
    skills = raw.get("skills", {})
    if not isinstance(skills, dict):
        raise ConfigError(f"{path}: 'skills' must be a map of skills")

    validated_skills: dict[str, dict[str, Any]] = {}
    for name, skill in skills.items():
        if not isinstance(skill, dict):
            logger.warning("Skill '%s' in %s is invalid, entry skipped", name, path)
            continue

        # Default origin is 'vault' when absent
        origin = skill.get("origin", "vault")
        if origin not in SKILL_ORIGINS:
            logger.warning("Skill '%s' has unknown origin '%s', handled with caution", name, origin)

        validated_skills[str(name)] = skill

    return {
        "schema_version": raw.get("schema_version", 1),
        "skills": validated_skills,
        "raw": raw,
    }



def load_council_config(path: Path) -> dict[str, Any]:
    """Loads council/seats.yaml tolerantly."""
    raw = _load_yaml(path, "Council configuration")
    return {
        "schema_version": raw.get("schema_version", 1),
        "seats": raw.get("seats", {}),
        "routing": raw.get("routing", {}),
        "raw": raw,
    }
