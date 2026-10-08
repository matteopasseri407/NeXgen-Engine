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

from nexgen_core.errors import NexgenError

logger = logging.getLogger("nexgen.config")

#: The four runtimes the layer knows how to configure. Applies to both the
#: MCP connectors and the skill views: it's the same list, and keeping it in
#: two separate constants is how the two lists end up drifting apart.
RUNTIME_TARGETS = frozenset({"claude", "codex", "antigravity", "opencode"})
SKILL_TARGETS = RUNTIME_TARGETS
SKILL_ORIGINS = frozenset({"vault", "engine", "github", "installer", "upstream"})
SKILL_EXPOSURES = frozenset({"lazy", "eager", "manual", "core"})


class ConfigError(NexgenError, ValueError):
    """Blocking configuration error (e.g. malformed YAML or missing required fields)."""


def _load_yaml(path: Path, label: str) -> dict[str, Any]:
    """Loads a YAML file, verifying the root is a mapping."""
    if not path.is_file():
        raise ConfigError(f"{label} not found: {path}")
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigError(f"{label} ({path}) is not valid UTF-8: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"Could not read {label} ({path}): {exc}") from exc
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML syntax in {label} ({path}): {exc}") from exc

    if data is None:
        raise ConfigError(f"{label} ({path}) is empty")
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
        var_name = var_name.strip()
        if var_name in ctx:
            return ctx[var_name]
        return os.environ.get(var_name, default_val)

    return re.sub(r"\$\{([^}]+)\}", _replace_match, text)


#: The one templating dialect the renderer speaks, deliberately tiny:
#: `{{ .var }}` for variables and `{{ if eq .os "x" }}A{{ else }}B{{ end }}`
#: for per-OS branches. It exists because the vault is one file shared by
#: Linux and Windows machines, and duplicating a whole server entry into a
#: `windows:` block because ONE arg carries a path is the hand-synced copy
#: the architecture forbids.
_IF_OPEN_RE = re.compile(r"\{\{\s*if\s+eq\s+\.([A-Za-z_][A-Za-z0-9_]*)\s+\"([^\"]*)\"\s*\}\}")
_IF_ELSE_RE = re.compile(r"\{\{\s*else\s*\}\}")
_IF_END_RE = re.compile(r"\{\{\s*end\s*\}\}")
_TOKEN_RE = re.compile(r"\{\{\s*(if\s+eq\s+\.[A-Za-z_][A-Za-z0-9_]*\s+\"[^\"]*\"|else|end)\s*\}\}")
_TEMPLATE_RE = re.compile(r"\{\{\s*(.+?)\s*\}\}")
_VAR_RE = re.compile(r"^\.([A-Za-z_][A-Za-z0-9_]*)$")


class TemplateError(ConfigError):
    """The manifest carries an inline template the engine cannot honor."""


def expand_inline_templates(text: str, context: dict[str, str]) -> str:
    """Expands `{{ .var }}` and `{{ if eq .var "x" }}…{{ else }}…{{ end }}`.

    Runs BEFORE `${VAR}` expansion, so a selected branch may itself carry
    environment references. Unknown variables and malformed blocks are
    errors, never silent passthrough: an unexpanded `{{ }}` would land in a
    CLI config as literal text, which is the kind of quiet wrong this
    engine exists to prevent.

    Nesting is supported via a depth-counted scan (the old non-greedy
    regex truncated on the first inner `{{ end }}`). Both branches are
    validated even when only one is selected: a typo in a Windows branch
    must fail on Linux too, otherwise it sleeps until a Windows user gets
    a broken config.
    """
    if "{{" not in text:
        return text
    text = _expand_if_blocks(text, context)

    def _expand_var(match: re.Match) -> str:
        block = match.group(1)
        var_match = _VAR_RE.match(block)
        if not var_match:
            # Stray control keywords outside a resolved block are malformed,
            # not variables.
            if re.fullmatch(r"(if\s+eq\s+\..*|else|end)", block):
                raise TemplateError(f"unmatched template block '{{{{ {block} }}}}'")
            raise TemplateError(f"unsupported template block '{{{{ {block} }}}}'")
        name = var_match.group(1)
        if name not in context:
            raise TemplateError(f"unknown template variable '.{name}'")
        return context[name]

    return _TEMPLATE_RE.sub(_expand_var, text)


def _expand_if_blocks(text: str, context: dict[str, str]) -> str:
    """Resolves all `{{ if }}…{{ else }}…{{ end }}` blocks, nesting-aware."""
    out: list[str] = []
    pos = 0
    while True:
        open_match = _IF_OPEN_RE.search(text, pos)
        if open_match is None:
            out.append(text[pos:])
            break
        name, wanted = open_match.group(1), open_match.group(2)
        if name not in context:
            raise TemplateError(f"template condition on unknown variable '.{name}'")
        open_end = open_match.end()
        then_part, else_part, block_end = _split_if_block(text, open_end)
        # Validate BOTH branches (recursion raises on malformed content);
        # only the selected one lands in the output.
        expanded_then = expand_inline_templates(then_part, context)
        expanded_else = expand_inline_templates(else_part, context) if else_part else ""
        out.append(text[pos:open_match.start()])
        out.append(expanded_then if context[name] == wanted else expanded_else)
        pos = block_end
    return "".join(out)


def _split_if_block(text: str, start: int) -> tuple[str, str, int]:
    """Splits one `{{ if }}` body into (then, else, end_pos), nesting-aware.

    `start` is the offset right after the opening `{{ if … }}`. Returns the
    raw then-part, the raw else-part ("" when no `{{ else }}`), and the
    offset right after the matching `{{ end }}`. Raises TemplateError on a
    missing `{{ end }}` or a stray `{{ else }}`.
    """
    depth = 1
    cursor = start
    else_start: int | None = None
    else_end: int | None = None
    while True:
        token = _TOKEN_RE.search(text, cursor)
        if token is None:
            raise TemplateError("unclosed template block: missing '{{ end }}'")
        kind = token.group(1)
        if kind.startswith("if"):
            depth += 1
        elif kind == "else":
            if depth == 1 and else_start is None:
                else_start, else_end = token.start(), token.end()
            # `else` at depth > 1 belongs to an inner block: skip it here,
            # the recursive expansion will handle it.
        elif kind == "end":
            depth -= 1
            if depth == 0:
                then_part = text[start:else_start if else_start is not None else token.start()]
                else_part = text[else_end:token.start()] if else_start is not None else ""
                return then_part, else_part, token.end()
        cursor = token.end()


def load_mcp_manifest(path: Path, *, strict: bool = False) -> dict[str, Any]:
    """Loads and validates mcp/manifest.yaml tolerantly.

    ``retired_servers`` is the explicit, cross-CLI removal mechanism: a name
    listed there disappears from the returned active connectors, so every
    consumer (the renderer, dependency watch) sees it exactly once. A name
    present both among the retired and among the active servers is a manifest
    error: the active entry is skipped with a warning, and the document is
    never rejected (invariant 8).

    ``strict=True`` turns skipped entries into one `ConfigError` naming them
    all: the guard preflight and the doctor validity check use it, so a typo
    can never silently drop a connector while every report says aligned.
    Unknown *fields* stay tolerated in both modes (forward compatibility).
    """
    raw = _load_yaml(path, "MCP manifest")
    servers = raw.get("servers", {})
    if not isinstance(servers, dict):
        raise ConfigError(f"{path}: 'servers' must be a map of connectors")

    retired_raw = raw.get("retired_servers", [])
    if not isinstance(retired_raw, list):
        logger.warning("'retired_servers' in %s must be a list, ignored", path)
        retired_raw = []
    retired_servers = {str(name).strip() for name in retired_raw if str(name).strip()}

    validated_servers: dict[str, dict[str, Any]] = {}
    problems: list[str] = []
    for name, srv in servers.items():
        label = str(name).strip()
        if not isinstance(srv, dict):
            problems.append(f"connector '{name}': not a map, entry skipped")
            continue

        # Check for essential fields
        cmd = srv.get("command") or srv.get("cmd")
        url = srv.get("url")
        if not cmd and not url:
            problems.append(f"connector '{name}': neither 'command' nor 'url', entry skipped")
            continue

        if label in retired_servers:
            problems.append(f"connector '{name}': both active and retired, entry skipped (stays retired)")
            continue

        validated_servers[label] = srv

    if problems:
        if strict:
            raise ConfigError(
                f"{path}: invalid connector entries: " + "; ".join(problems)
            )
        for problem in problems:
            logger.warning("%s in %s", problem, path)

    return {
        "schema_version": raw.get("schema_version", 1),
        "servers": validated_servers,
        "retired_servers": retired_servers,
        "hooks": raw.get("hooks", []),
        "raw": raw,
    }


def load_skills_manifest(path: Path, *, strict: bool = False) -> dict[str, Any]:
    """Loads and validates skills.manifest.yaml tolerantly.

    ``strict=True`` turns structurally invalid entries into one `ConfigError`
    (unknown *origins* stay tolerated in both modes: a machine receiving a
    skill from a future origin must warn and continue, never stop).
    """
    raw = _load_yaml(path, "Skills manifest")
    skills = raw.get("skills", {})
    if not isinstance(skills, dict):
        raise ConfigError(f"{path}: 'skills' must be a map of skills")

    validated_skills: dict[str, dict[str, Any]] = {}
    problems: list[str] = []
    for name, skill in skills.items():
        if not isinstance(skill, dict):
            problems.append(f"skill '{name}': not a map, entry skipped")
            continue

        # Default origin is 'vault' when absent
        origin = skill.get("origin", "vault")
        if origin not in SKILL_ORIGINS:
            logger.warning("Skill '%s' has unknown origin '%s', handled with caution", name, origin)

        validated_skills[str(name).strip()] = skill

    if problems:
        if strict:
            raise ConfigError(
                f"{path}: invalid skill entries: " + "; ".join(problems)
            )
        for problem in problems:
            logger.warning("%s in %s", problem, path)

    return {
        "schema_version": raw.get("schema_version", 1),
        "skills": validated_skills,
        "raw": raw,
    }



def load_council_config(path: Path) -> dict[str, Any]:
    """Loads council/seats.yaml tolerantly."""
    raw = _load_yaml(path, "Council configuration")
    for field in ("seats", "routing", "sequences"):
        if field in raw and raw[field] is not None and not isinstance(raw[field], dict):
            raise ConfigError(f"Council {field} must be a map/dictionary")
    seats = raw.get("seats") or {}
    if not isinstance(seats, dict):
        raise ConfigError("Council seats must be a map/dictionary")
    for name, seat in seats.items():
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
            raise ConfigError("Council seat names must be safe file names")
        if not isinstance(seat, dict):
            raise ConfigError(f"Council seat {name} must be a map/dictionary")
        for field in ("cli", "model", "vendor"):
            if not isinstance(seat.get(field), str) or not seat[field].strip():
                raise ConfigError(f"Council seat {name} needs a non-empty {field}")
        if "zero_retention" in seat and not isinstance(seat["zero_retention"], bool):
            raise ConfigError(f"Council seat {name}: zero_retention must be boolean")
    return {
        "schema_version": raw.get("schema_version", 1),
        "seats": seats,
        "routing": raw.get("routing") or {},
        "sequences": raw.get("sequences") or {},
        "raw": raw,
    }
