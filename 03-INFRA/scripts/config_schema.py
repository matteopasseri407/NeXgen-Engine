#!/usr/bin/env python3
"""Executable contracts for the data that drives NeXgen runtime writes.

The engine intentionally keeps user configuration in the private data root.
That makes validation a control-plane concern: malformed data must be rejected
before a renderer, a synchronizer, or a hook mutates a derived runtime file.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import yaml

# TOML read-only, for the Codex posture renderer's before/after verification
# (never for writing -- surgical text edits below do that). Same fallback
# chain as mcp/render.py: stdlib on 3.11+, the README-documented `tomli`
# backport on a supported 3.10 host, otherwise the renderer degrades to
# "unavailable" instead of crashing this whole module's import.
try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        tomllib = None


MCP_SCHEMA_VERSION = 1
COUNCIL_SCHEMA_VERSION = 1
PERMISSIONS_SCHEMA_VERSION = 1
MCP_TARGETS = frozenset({"claude", "codex", "antigravity", "opencode"})
COUNCIL_CLIS = frozenset({"opencode", "agy", "codex", "claude", "ollama"})
COUNCIL_REASONING_EFFORTS = frozenset({"none", "low", "medium", "high", "xhigh", "max"})
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ENTRY_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# Exact npm package pin (e.g. "firecrawl-mcp@3.22.3" or "@org/pkg@1.2.3"): an
# npx server without this can silently resolve to whatever the registry's
# "latest" happens to be at run time. Kept identical to the constant of the
# same name in tests/test_mcp_package_pins.py (EXACT_NPM_PIN) so the two
# never drift into checking different things.
EXACT_NPM_PIN_RE = re.compile(r"^(?:@[-a-z0-9_.]+/)?[-a-z0-9_.]+@\d+(?:\.\d+){2}$", re.I)

# Heuristic for "this string looks like a literal secret, not a reference".
# Duplicated from mcp/render.py's LONGTOK (import would be circular: render.py
# imports this module) -- keep the two patterns identical if either changes.
LONGTOK_RE = re.compile(r"^[A-Za-z0-9_\-\.=+/]{40,}$")


class ConfigValidationError(ValueError):
    """A user-owned configuration does not satisfy its executable contract."""


def _jsonc_without_comments(text: str) -> str:
    """Replace JSONC comments with whitespace while preserving byte offsets.

    Keeping newlines and character positions stable makes parser errors and
    surgical config edits point at the original document. Comment markers
    inside JSON strings are left untouched.
    """
    out = list(text)
    i = 0
    in_string = False
    escaped = False
    while i < len(text):
        char = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            i += 1
            continue
        if char == '"':
            in_string = True
            i += 1
            continue
        if char == "/" and i + 1 < len(text) and text[i + 1] == "/":
            out[i] = out[i + 1] = " "
            i += 2
            while i < len(text) and text[i] not in "\r\n":
                out[i] = " "
                i += 1
            continue
        if char == "/" and i + 1 < len(text) and text[i + 1] == "*":
            out[i] = out[i + 1] = " "
            i += 2
            closed = False
            while i < len(text):
                if text[i] == "*" and i + 1 < len(text) and text[i + 1] == "/":
                    out[i] = out[i + 1] = " "
                    i += 2
                    closed = True
                    break
                if text[i] not in "\r\n":
                    out[i] = " "
                i += 1
            if not closed:
                raise ValueError("unterminated JSONC block comment")
            continue
        i += 1
    return "".join(out)


def _jsonc_without_trailing_commas(text: str) -> str:
    out = list(text)
    i = 0
    in_string = False
    escaped = False
    while i < len(text):
        char = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            i += 1
            continue
        if char == '"':
            in_string = True
            i += 1
            continue
        if char == ",":
            lookahead = i + 1
            while lookahead < len(text) and text[lookahead].isspace():
                lookahead += 1
            if lookahead < len(text) and text[lookahead] in "]}":
                out[i] = " "
        i += 1
    return "".join(out)


def parse_jsonc(text: str) -> Any:
    """Parse the JSON-with-comments dialect accepted by current OpenCode."""
    return json.loads(_jsonc_without_trailing_commas(_jsonc_without_comments(text)))


def _skip_jsonc_trivia(text: str, start: int) -> int:
    i = start
    while i < len(text):
        if text[i].isspace():
            i += 1
            continue
        if text.startswith("//", i):
            newline = text.find("\n", i + 2)
            return len(text) if newline < 0 else _skip_jsonc_trivia(text, newline + 1)
        if text.startswith("/*", i):
            close = text.find("*/", i + 2)
            return len(text) if close < 0 else _skip_jsonc_trivia(text, close + 2)
        break
    return i


def _jsonc_string_end(text: str, start: int) -> int:
    i = start + 1
    escaped = False
    while i < len(text):
        if escaped:
            escaped = False
        elif text[i] == "\\":
            escaped = True
        elif text[i] == '"':
            return i + 1
        i += 1
    raise ValueError("unterminated JSON string")


def _jsonc_value_end(text: str, start: int) -> int:
    start = _skip_jsonc_trivia(text, start)
    if start >= len(text):
        raise ValueError("missing JSON value")
    if text[start] == '"':
        return _jsonc_string_end(text, start)
    if text[start] not in "[{":
        i = start
        while i < len(text) and text[i] not in ",]}":
            i += 1
        return i

    stack = [text[start]]
    i = start + 1
    while i < len(text):
        if text[i] == '"':
            i = _jsonc_string_end(text, i)
            continue
        if text.startswith("//", i):
            newline = text.find("\n", i + 2)
            i = len(text) if newline < 0 else newline + 1
            continue
        if text.startswith("/*", i):
            close = text.find("*/", i + 2)
            if close < 0:
                raise ValueError("unterminated JSONC block comment")
            i = close + 2
            continue
        if text[i] in "[{":
            stack.append(text[i])
        elif text[i] in "]}":
            expected = "[" if text[i] == "]" else "{"
            if not stack or stack[-1] != expected:
                raise ValueError("mismatched JSON delimiters")
            stack.pop()
            if not stack:
                return i + 1
        i += 1
    raise ValueError("unterminated JSON value")


def jsonc_top_level_value_span(text: str, key: str) -> tuple[int, int] | None:
    """Return the value span for one top-level JSONC property."""
    root = _skip_jsonc_trivia(text, 0)
    if root >= len(text) or text[root] != "{":
        raise ValueError("JSONC root is not an object")
    i = root + 1
    while True:
        i = _skip_jsonc_trivia(text, i)
        if i >= len(text):
            raise ValueError("unterminated JSONC root object")
        if text[i] == "}":
            return None
        if text[i] != '"':
            raise ValueError("top-level JSONC property name is not a string")
        name_end = _jsonc_string_end(text, i)
        name = json.loads(text[i:name_end])
        colon = _skip_jsonc_trivia(text, name_end)
        if colon >= len(text) or text[colon] != ":":
            raise ValueError("missing colon after top-level JSONC property")
        value_start = _skip_jsonc_trivia(text, colon + 1)
        value_end = _jsonc_value_end(text, value_start)
        if name == key:
            return value_start, value_end
        i = _skip_jsonc_trivia(text, value_end)
        if i < len(text) and text[i] == ",":
            i += 1
            continue
        if i < len(text) and text[i] == "}":
            return None
        raise ValueError("missing comma after top-level JSONC property")


def set_jsonc_top_level_value(text: str, key: str, value: Any) -> str:
    """Surgically set one top-level value while preserving other comments."""
    parsed = parse_jsonc(text)
    if not isinstance(parsed, dict):
        raise ValueError("JSONC root is not an object")
    serialized = json.dumps(value, ensure_ascii=False, indent=2)
    span = jsonc_top_level_value_span(text, key)
    if span is not None:
        start, end = span
        line_start = text.rfind("\n", 0, start) + 1
        indent_match = re.match(r"[ \t]*", text[line_start:start])
        indent = indent_match.group(0) if indent_match else ""
        if "\n" in serialized:
            lines = serialized.splitlines()
            serialized = lines[0] + "\n" + "\n".join(indent + line for line in lines[1:])
        result = text[:start] + serialized + text[end:]
    else:
        root_start = _skip_jsonc_trivia(text, 0)
        root_end = _jsonc_value_end(text, root_start) - 1
        indent = "  "
        uncommented = _jsonc_without_comments(text)
        match = re.search(r'(?m)^([ \t]+)"', uncommented[root_start + 1:root_end])
        if match:
            indent = match.group(1)
        value_lines = serialized.splitlines()
        rendered = value_lines[0]
        if len(value_lines) > 1:
            rendered += "\n" + "\n".join(indent + line for line in value_lines[1:])
        result = (
            text[:root_start + 1]
            + "\n"
            + indent
            + json.dumps(key, ensure_ascii=False)
            + ": "
            + rendered
            + ("," if parsed else "")
            + text[root_start + 1:]
        )
    reparsed = parse_jsonc(result)
    if not isinstance(reparsed, dict) or reparsed.get(key) != value:
        raise ValueError(f"failed to set top-level JSONC property {key!r}")
    return result


def toml_reader_available() -> bool:
    """False on a Python 3.10 host with no `tomli` installed (README's
    documented minimum). The Codex posture renderer treats that as an
    environment capability gap -- warn and leave codex unapplied, same as an
    unrenderable CLI -- never a hard failure of the whole permissions phase."""
    return tomllib is not None


def parse_toml(text: str) -> dict[str, Any]:
    """tomllib.loads with the module-level tomli fallback resolved, and a
    genuine parse error turned into the caller-friendly exception type.
    Callers must check toml_reader_available() first -- this raises
    ConfigValidationError if there is no reader at all, which a caller that
    skipped that check would otherwise mistake for "the file is malformed".
    Read-only -- the Codex posture renderer uses this only to check the
    current value and to verify the surgical edit below afterwards."""
    if tomllib is None:
        raise ConfigValidationError(
            "no TOML reader available (Python 3.11+, or Python 3.10 with 'tomli' installed)"
        )
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigValidationError(f"invalid TOML: {exc}") from exc


def _toml_root_table_end(lines: list[str]) -> int:
    """Index of the first `[table]` (or `[[array-of-tables]]`) header line --
    i.e. where the root table's own bare `key = value` lines stop. Every
    line before it (including blank lines and comments) is still root-table
    territory and safe to insert into."""
    for i, line in enumerate(lines):
        if re.match(r"^[ \t]*\[", line):
            return i
    return len(lines)


def set_toml_root_string(text: str, key: str, value: str) -> str:
    """Surgically set one ROOT-TABLE string key (e.g. Codex's
    approval_policy/sandbox_mode) in place: touches only that one line (or
    inserts one new line ahead of the first [table] header) and leaves every
    comment, blank line, and [table]/[[array]] section untouched -- the
    'modifica chirurgica, non riscrittura' contract for Codex's config.toml.
    Mirrors set_jsonc_top_level_value's contract: the result is re-parsed and
    the new value verified before it is ever returned, so a bug here fails
    loudly instead of silently corrupting a user's config.toml.
    """
    if not re.fullmatch(r"[A-Za-z0-9_-]+", key):
        raise ValueError(f"unsupported TOML root key spelling: {key!r}")
    lines = text.split("\n")
    root_end = _toml_root_table_end(lines)
    pattern = re.compile(rf"^[ \t]*{re.escape(key)}[ \t]*=.*$")
    rendered = f"{key} = {json.dumps(value)}"
    for i in range(root_end):
        if pattern.match(lines[i]):
            lines[i] = rendered
            break
    else:
        lines.insert(root_end, rendered)
    result = "\n".join(lines)
    reparsed = parse_toml(result)
    if reparsed.get(key) != value:
        raise ValueError(f"failed to set TOML root key {key!r}")
    return result


def _error(source: str | Path, message: str) -> None:
    raise ConfigValidationError(f"{source}: {message}")


def _load_yaml_mapping(path: Path, label: str) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        _error(path, f"cannot read {label}: {exc}")
    except yaml.YAMLError as exc:
        _error(path, f"invalid {label} YAML: {exc}")
    if not isinstance(data, dict):
        _error(path, f"{label} root must be a mapping")
    return data


def _reject_unknown_keys(mapping: dict[str, Any], allowed: set[str], source: str | Path, where: str) -> None:
    unknown = sorted(str(key) for key in mapping if key not in allowed)
    if unknown:
        _error(source, f"{where} has unsupported field(s): {', '.join(unknown)}")


def _mapping(value: Any, source: str | Path, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _error(source, f"{where} must be a mapping")
    return value


def _nonempty_string(value: Any, source: str | Path, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _error(source, f"{where} must be a non-empty string")
    return value


def _env_name(value: Any, source: str | Path, where: str) -> str:
    value = _nonempty_string(value, source, where)
    if not ENV_NAME_RE.fullmatch(value):
        _error(source, f"{where} must be a valid environment variable name")
    return value


def _positive_number(value: Any, source: str | Path, where: str) -> float:
    if isinstance(value, bool):
        _error(source, f"{where} must be a finite number greater than zero")
    try:
        number = float(value)
    except (TypeError, ValueError):
        _error(source, f"{where} must be a finite number greater than zero")
    if not math.isfinite(number) or number <= 0:
        _error(source, f"{where} must be a finite number greater than zero")
    return number


def _string_list(value: Any, source: str | Path, where: str, *, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        _error(source, f"{where} must be a list of non-empty strings")
    if not allow_empty and not value:
        _error(source, f"{where} must not be empty")
    return value


def _routing_config(value: Any, source: str | Path) -> None:
    routing = _mapping(value, source, "Council routing config")
    _reject_unknown_keys(routing, {"enabled", "decision_file", "mode_defaults", "relay_roles"}, source, "Council routing config")
    if type(routing.get("enabled")) is not bool:
        _error(source, "Council routing config.enabled must be true or false")
    if routing["enabled"]:
        decision_file = _nonempty_string(routing.get("decision_file"), source, "Council routing config.decision_file")
        path = PurePosixPath(decision_file)
        windows_path = PureWindowsPath(decision_file)
        if path.is_absolute() or windows_path.is_absolute() or "\\" in decision_file or ".." in path.parts:
            _error(source, "Council routing config.decision_file must be a relative Vault path without '..'")
    elif "decision_file" in routing:
        _nonempty_string(routing["decision_file"], source, "Council routing config.decision_file")
    if "mode_defaults" in routing:
        defaults = _mapping(routing["mode_defaults"], source, "Council routing config.mode_defaults")
        allowed_modes = {"brainstorm", "challenge", "code-review"}
        _reject_unknown_keys(defaults, allowed_modes, source, "Council routing config.mode_defaults")
        for mode, role in defaults.items():
            _nonempty_string(role, source, f"Council routing config.mode_defaults.{mode}")
    if "relay_roles" in routing:
        _string_list(routing["relay_roles"], source, "Council routing config.relay_roles", allow_empty=False)


def _looks_like_env_reference(value: str) -> bool:
    """True if a stdio env value defers to another env var instead of
    embedding a value directly (render.py's own redact() uses this same
    signal to decide what is safe to print)."""
    return "${" in value or "{env:" in value


def _looks_like_secret_literal(value: str) -> bool:
    """Same heuristic as render.py's redact(): a long token-charset string
    that contains at least one digit reads as a pasted credential, not
    ordinary config data (hostnames, flags, short numbers stay well under the
    40-char floor)."""
    return bool(LONGTOK_RE.match(value)) and any(c.isdigit() for c in value)


def _env_mapping(value: Any, source: str | Path, where: str) -> dict[str, str]:
    mapping = _mapping(value, source, where)
    for key, item in mapping.items():
        _env_name(key, source, f"{where} key")
        if not isinstance(item, str):
            _error(source, f"{where}.{key} must be a string")
        if _looks_like_secret_literal(item) and not _looks_like_env_reference(item):
            _error(
                source,
                f"{where}.{key} looks like a literal secret value, not a reference -- "
                "point it at an environment variable (e.g. \"${VAR}\") instead of embedding the value",
            )
    return mapping


def _validate_auth(value: Any, source: str | Path, where: str) -> None:
    auth = _mapping(value, source, where)
    _reject_unknown_keys(auth, {"type", "env"}, source, where)
    if auth.get("type") != "bearer":
        _error(source, f"{where}.type must be 'bearer'")
    _env_name(auth.get("env"), source, f"{where}.env")


def _validate_timeouts(value: Any, source: str | Path, where: str) -> None:
    timeouts = _mapping(value, source, where)
    _reject_unknown_keys(timeouts, {"startup", "tool"}, source, where)
    if not timeouts:
        _error(source, f"{where} must contain startup and/or tool")
    for key, item in timeouts.items():
        _positive_number(item, source, f"{where}.{key}")


def _validate_npx_pin(args: Any, source: str | Path, where: str) -> None:
    """An npx stdio server without an exact version pin resolves to whatever
    "latest" is on the npm registry at process-start time -- a silent
    supply-chain door. Mirrors tests/test_mcp_package_pins.py's
    EXACT_NPM_PIN check, but as a real validation gate: that test only ever
    ran against the repo's template manifest, never against the actual
    manifest.yaml render.py loads from AGENT_VAULT_DATA at runtime."""
    values = args if isinstance(args, list) else []
    package = next((item for item in values if isinstance(item, str) and not item.startswith("-")), None)
    if package is None or not EXACT_NPM_PIN_RE.fullmatch(package):
        _error(
            source,
            f"{where}.args must pin the npx package to an exact version (e.g. 'package@1.2.3' "
            f"or '@scope/package@1.2.3'), got {package!r}",
        )


def _validate_mcp_server(
    server: Any,
    source: str | Path,
    where: str,
    *,
    allow_windows: bool,
) -> None:
    spec = _mapping(server, source, where)
    allowed = {
        "transport",
        "command",
        "args",
        "env",
        "require_env",
        "targets",
        "url",
        "url_env",
        "auth",
        "timeouts",
    }
    if allow_windows:
        allowed.add("windows")
    _reject_unknown_keys(spec, allowed, source, where)

    transport = spec.get("transport")
    if transport not in {"stdio", "http"}:
        _error(source, f"{where}.transport must be 'stdio' or 'http'")
    targets = _string_list(spec.get("targets"), source, f"{where}.targets", allow_empty=False)
    if len(set(targets)) != len(targets):
        _error(source, f"{where}.targets must not contain duplicates")
    unsupported_targets = sorted(set(targets) - MCP_TARGETS)
    if unsupported_targets:
        _error(source, f"{where}.targets contains unsupported CLI(s): {', '.join(unsupported_targets)}")

    if "require_env" in spec:
        _env_name(spec["require_env"], source, f"{where}.require_env")
    if "timeouts" in spec:
        _validate_timeouts(spec["timeouts"], source, f"{where}.timeouts")

    if transport == "stdio":
        command = _nonempty_string(spec.get("command"), source, f"{where}.command")
        args = spec.get("args")
        if "args" in spec:
            args = _string_list(spec["args"], source, f"{where}.args")
        if command == "npx":
            _validate_npx_pin(args, source, where)
        if "env" in spec:
            _env_mapping(spec["env"], source, f"{where}.env")
        for field in ("url", "url_env", "auth"):
            if field in spec:
                _error(source, f"{where}.{field} is only valid for transport 'http'")
    else:
        _nonempty_string(spec.get("url"), source, f"{where}.url")
        _validate_auth(spec.get("auth"), source, f"{where}.auth")
        if "url_env" in spec:
            _env_name(spec["url_env"], source, f"{where}.url_env")
        for field in ("command", "args", "env"):
            if field in spec:
                _error(source, f"{where}.{field} is only valid for transport 'stdio'")

    if allow_windows and "windows" in spec:
        windows = _mapping(spec["windows"], source, f"{where}.windows")
        override_fields = {
            "command",
            "args",
            "env",
            "require_env",
            "url",
            "url_env",
            "auth",
            "timeouts",
        }
        _reject_unknown_keys(windows, override_fields, source, f"{where}.windows")
        merged = {key: value for key, value in spec.items() if key != "windows"}
        merged.update(windows)
        _validate_mcp_server(merged, source, f"{where}.windows", allow_windows=False)


def validate_mcp_manifest(data: Any, source: str | Path) -> dict[str, dict[str, Any]]:
    manifest = _mapping(data, source, "MCP manifest")
    _reject_unknown_keys(manifest, {"schema_version", "servers", "retired_servers"}, source, "MCP manifest")
    if type(manifest.get("schema_version")) is not int or manifest["schema_version"] != MCP_SCHEMA_VERSION:
        _error(source, f"MCP manifest schema_version must be {MCP_SCHEMA_VERSION}")
    servers = _mapping(manifest.get("servers"), source, "MCP manifest.servers")
    retired = _string_list(
        manifest.get("retired_servers", []),
        source,
        "MCP manifest.retired_servers",
    )
    if len(set(retired)) != len(retired):
        _error(source, "MCP manifest.retired_servers must not contain duplicates")
    for name in retired:
        if not ENTRY_NAME_RE.fullmatch(name):
            _error(source, "every retired MCP server name must use letters, digits, '.', '_' or '-'")
        if name in servers:
            _error(source, f"MCP server '{name}' cannot be both active and retired")
    for name, server in servers.items():
        if not isinstance(name, str) or not ENTRY_NAME_RE.fullmatch(name):
            _error(source, "every MCP server name must use letters, digits, '.', '_' or '-'")
        _validate_mcp_server(server, source, f"MCP server '{name}'", allow_windows=True)
    # Codex maps hyphens to underscores in TOML table names. Two otherwise
    # valid manifest names can therefore collapse to one live key and launch
    # duplicate or ambiguous clients. Reject the collision before any writer
    # touches a runtime config.
    codex_keys: dict[str, str] = {}
    for name, server in servers.items():
        if "codex" not in server.get("targets", []):
            continue
        key = name.replace("-", "_").casefold()
        previous = codex_keys.get(key)
        if previous is not None and previous != name:
            _error(
                source,
                f"MCP servers '{previous}' and '{name}' collide as Codex key '{key}'",
            )
        codex_keys[key] = name
    for name in retired:
        key = name.replace("-", "_").casefold()
        active = codex_keys.get(key)
        if active is not None:
            _error(
                source,
                f"retired MCP server '{name}' collides with active Codex server '{active}' as key '{key}'",
            )
    return servers


def load_mcp_manifest_document(path: Path) -> tuple[dict[str, dict[str, Any]], tuple[str, ...]]:
    data = _load_yaml_mapping(path, "MCP manifest")
    servers = validate_mcp_manifest(data, path)
    return servers, tuple(data.get("retired_servers", []))


def load_mcp_manifest(path: Path) -> dict[str, dict[str, Any]]:
    return load_mcp_manifest_document(path)[0]


def _sequence_candidates(value: Any, source: str | Path, where: str) -> list[str]:
    if isinstance(value, str):
        item = value.strip()
        if not item or "=" not in item or "," in item:
            _error(source, f"{where} string must use role=seat or role=seat|fallback")
        role, candidates = item.split("=", 1)
        if not role.strip():
            _error(source, f"{where} needs a non-empty role")
        names = [name.strip() for name in candidates.split("|") if name.strip()]
        if not names:
            _error(source, f"{where} needs at least one seat")
        return names

    stage = _mapping(value, source, where)
    _reject_unknown_keys(stage, {"role", "seat", "seats", "fallback"}, source, where)
    _nonempty_string(stage.get("role"), source, f"{where}.role")
    has_seat = "seat" in stage
    has_seats = "seats" in stage
    if has_seat == has_seats:
        _error(source, f"{where} needs exactly one of seat or seats")
    if has_seat:
        candidates = [_nonempty_string(stage["seat"], source, f"{where}.seat")]
    else:
        candidates = _string_list(stage["seats"], source, f"{where}.seats", allow_empty=False)
    if "fallback" in stage:
        fallback = stage["fallback"]
        if isinstance(fallback, str):
            candidates.append(_nonempty_string(fallback, source, f"{where}.fallback"))
        else:
            candidates.extend(_string_list(fallback, source, f"{where}.fallback", allow_empty=False))
    return candidates


def _validate_sequence(value: Any, source: str | Path, where: str) -> list[str]:
    if not isinstance(value, list) or not value:
        _error(source, f"{where} must be a non-empty list of relay stages")
    return [candidate for index, item in enumerate(value) for candidate in _sequence_candidates(item, source, f"{where}[{index}]")]


def validate_council_config(data: Any, source: str | Path) -> dict[str, Any]:
    config = _mapping(data, source, "Council seats config")
    _reject_unknown_keys(config, {"schema_version", "seats", "sequence", "sequences", "routing"}, source, "Council seats config")
    if type(config.get("schema_version")) is not int or config["schema_version"] != COUNCIL_SCHEMA_VERSION:
        _error(source, f"Council seats config schema_version must be {COUNCIL_SCHEMA_VERSION}")
    seats = _mapping(config.get("seats"), source, "Council seats config.seats")
    for name, seat in seats.items():
        if not isinstance(name, str) or not ENTRY_NAME_RE.fullmatch(name):
            _error(source, "every Council seat name must use letters, digits, '.', '_' or '-'")
        spec = _mapping(seat, source, f"Council seat '{name}'")
        _reject_unknown_keys(
            spec,
            {
                "vendor", "cli", "model", "quota_pool", "timeout_seconds", "zero_retention",
                "routing_id", "routing_label", "reasoning_effort",
            },
            source,
            f"Council seat '{name}'",
        )
        _nonempty_string(spec.get("vendor"), source, f"Council seat '{name}'.vendor")
        cli = _nonempty_string(spec.get("cli"), source, f"Council seat '{name}'.cli")
        if cli not in COUNCIL_CLIS:
            _error(source, f"Council seat '{name}'.cli must be one of: {', '.join(sorted(COUNCIL_CLIS))}")
        _nonempty_string(spec.get("model"), source, f"Council seat '{name}'.model")
        if type(spec.get("zero_retention")) is not bool:
            _error(source, f"Council seat '{name}'.zero_retention must be true or false")
        if "quota_pool" in spec:
            _nonempty_string(spec["quota_pool"], source, f"Council seat '{name}'.quota_pool")
        if "timeout_seconds" in spec:
            _positive_number(spec["timeout_seconds"], source, f"Council seat '{name}'.timeout_seconds")
        if "routing_id" in spec:
            _nonempty_string(spec["routing_id"], source, f"Council seat '{name}'.routing_id")
        if "routing_label" in spec:
            _nonempty_string(spec["routing_label"], source, f"Council seat '{name}'.routing_label")
        if "reasoning_effort" in spec:
            effort = _nonempty_string(spec["reasoning_effort"], source, f"Council seat '{name}'.reasoning_effort")
            if effort not in COUNCIL_REASONING_EFFORTS:
                _error(
                    source,
                    f"Council seat '{name}'.reasoning_effort must be one of: {', '.join(sorted(COUNCIL_REASONING_EFFORTS))}",
                )

    if "routing" in config:
        _routing_config(config["routing"], source)

    references: list[str] = []
    if "sequence" in config:
        references.extend(_validate_sequence(config["sequence"], source, "Council seats config.sequence"))
    if "sequences" in config:
        sequences = _mapping(config["sequences"], source, "Council seats config.sequences")
        for name, sequence in sequences.items():
            if not isinstance(name, str) or not name.strip():
                _error(source, "every named Council sequence needs a non-empty name")
            references.extend(_validate_sequence(sequence, source, f"Council sequence '{name}'"))
    unknown_references = sorted(set(references) - set(seats))
    if unknown_references:
        _error(source, f"Council sequence references unknown seat(s): {', '.join(unknown_references)}")
    return config


def load_council_config(path: Path) -> dict[str, Any]:
    return validate_council_config(_load_yaml_mapping(path, "Council seats config"), path)


def validate_claude_settings(path: Path) -> None:
    """Validate only the part of Claude settings that NeXgen may merge.

    Claude owns the rest of settings.json. Rejecting an unrelated future key
    would be brittle, but an invalid hooks shape would otherwise let a run copy
    the hook file and fail only halfway through its own mutation.
    """
    if not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _error(path, f"invalid Claude settings JSON: {exc}")
    if not isinstance(data, dict):
        _error(path, "Claude settings root must be an object")
    hooks = data.get("hooks")
    if hooks is None:
        return
    if not isinstance(hooks, dict):
        _error(path, "Claude settings hooks must be an object")
    # Every event NeXgen may merge into, not just the two the checkpoint hook
    # uses. claude_permissions writes PreToolUse, so leaving it out made the
    # preflight pass on exactly the shape that phase then had to handle.
    for event in sorted({"SessionStart", "PreCompact"} | PERMISSION_HOOK_EVENTS):
        if event not in hooks:
            continue
        matchers = hooks[event]
        if not isinstance(matchers, list):
            _error(path, f"Claude settings hooks.{event} must be a list")
        for matcher_index, matcher in enumerate(matchers):
            if not isinstance(matcher, dict):
                _error(path, f"Claude settings hooks.{event}[{matcher_index}] must be an object")
            entries = matcher.get("hooks", [])
            if not isinstance(entries, list):
                _error(path, f"Claude settings hooks.{event}[{matcher_index}].hooks must be a list")
            for hook_index, hook in enumerate(entries):
                if not isinstance(hook, dict):
                    _error(
                        path,
                        f"Claude settings hooks.{event}[{matcher_index}].hooks[{hook_index}] must be an object",
                    )


# Neutral posture vocabulary -> the value each CLI actually understands. Kept
# here, not in the manifest, so the private policy file stays free of any one
# vendor's spelling and a dialect change is an engine fix, not a user edit.
PERMISSION_POSTURES = {
    "bypass": "bypassPermissions",
    "accept-edits": "acceptEdits",
    "ask": "default",
}

# Per-CLI posture VALUES this engine has a verified renderer for -- confirmed
# against a real installed CLI (config live on a real machine + literal
# strings read out of the installed binary itself), not inferred from a
# schema or guessed. A CLI missing from this dict, or present but missing one
# of these values, has NO verified renderer here: agent_sync.py's dispatcher
# must warn and leave that CLI's posture unapplied rather than emit a config
# that looks applied and protects nothing (see the 2026-07-30 CLI permissions
# recon; guessing a dialect is banned by project policy). This is deliberately
# NOT enforced inside validate_permissions_manifest(): a (cli, posture) pair
# the engine cannot yet render is real user policy for a future dialect, not
# malformed data -- rejecting the whole manifest for it took Claude's own
# posture down with it (the 2026-07-30 'unsupported CLI codex' incident).
PERMISSION_RENDERERS: dict[str, frozenset[str]] = {
    "claude": frozenset({"bypass", "accept-edits", "ask"}),
    "codex": frozenset({"bypass"}),
    "opencode": frozenset({"bypass", "accept-edits"}),
    "antigravity": frozenset({"bypass"}),
}

# Codex: approval_policy/sandbox_mode, root-level TOML keys. Only 'bypass' is
# verified (matches the installed binary's own one-shot
# --dangerously-bypass-approvals-and-sandbox flag); accept-edits/ask have no
# confirmed default out-of-the-box, so they are absent here on purpose.
CODEX_POSTURE_RENDER: dict[str, dict[str, str]] = {
    "bypass": {"approval_policy": "never", "sandbox_mode": "danger-full-access"},
}
# OpenCode: permission.edit / permission.bash, read from the installed
# @opencode-ai/sdk type definitions. Only edit/bash are touched -- the other
# permission.* dimensions (webfetch, doom_loop, external_directory) are
# outside this engine's neutral bypass/accept-edits/ask vocabulary.
OPENCODE_POSTURE_RENDER: dict[str, dict[str, str]] = {
    "bypass": {"edit": "allow", "bash": "allow"},
    "accept-edits": {"edit": "allow", "bash": "ask"},
}
# Antigravity: toolPermission governs shell commands, artifactReviewPolicy
# governs file edits -- there is no clean accept-edits split (toolPermission
# has no file-edit equivalent), so only bypass -- writing BOTH keys, or a
# shell-only "bypass" would leave file edits still gated -- is verified.
ANTIGRAVITY_POSTURE_RENDER: dict[str, dict[str, str]] = {
    "bypass": {"toolPermission": "always-proceed", "artifactReviewPolicy": "always-proceed"},
}

# Verified guardrail-HOOK wiring targets -- separate from PERMISSION_RENDERERS
# above: a CLI can have a verified posture renderer (e.g. Codex bypass) while
# still having no wired PreToolUse-style guardrail hook at all. Declaring a
# hook for any CLI NOT in this set is still a hard manifest error, not a
# warn-and-skip, because porting the guardrail itself for that CLI is
# unstarted work, not something this engine should silently pretend to
# support (see the CLI permissions recon).
#
# Codex is deliberately absent. Its I/O contract (deny/allow/ask,
# permissionDecisionReason) is verified from the installed binary's own
# embedded schema -- as solid as Claude's -- but Codex gates every hook
# behind a persisted, per-hash TRUST prompt a human must accept once
# interactively, or an explicit `--dangerously-bypass-hook-trust` flag a
# provisioner would have to pass itself. A hook this engine writes would
# therefore not run at all until one of those happens, which is a silent,
# structural gap wearing the appearance of a working guardrail -- worse than
# having none. Declaring codex here requires the owner to first decide,
# explicitly, which of the two trust paths to take; guessing is not an
# option. OpenCode and Antigravity have no such gate (see the recon): a
# hook/plugin placed in their own config runs unconditionally on the next
# launch, which is exactly what makes them safely implementable today.
PERMISSION_HOOK_TARGETS = {"claude", "opencode", "antigravity"}
PERMISSION_HOOK_EVENTS = {
    "PreToolUse",
    "PostToolUse",
    "SessionStart",
    "SessionEnd",
    "PreCompact",
    "PostCompact",
    "UserPromptSubmit",
    "Stop",
}

# Per-CLI hook EVENTS this engine has a verified adapter for -- a second,
# finer axis than PERMISSION_HOOK_TARGETS (which only says "some wiring
# exists"). Claude's own dedicated wiring (_apply_claude_permissions in
# agent_sync.py) accepts every event in PERMISSION_HOOK_EVENTS already.
# OpenCode and Antigravity's adapters exist ONLY for the PreToolUse-shaped,
# shell-command guardrail this whole feature is about: OpenCode has no
# decision-bearing hook for any other lifecycle point at all (see the recon
# -- `tool.execute.before` cannot deny), and Antigravity's
# PreInvocation/PostInvocation/Stop hooks were never exercised against this
# engine. A manifest hook naming an event outside this set for one of these
# two targets is refused for THAT target alone (agent_sync._guardrail_specs_for),
# never guessed at, exactly like an unverified posture value above.
VERIFIED_HOOK_EVENTS: dict[str, frozenset[str]] = {
    "claude": frozenset(PERMISSION_HOOK_EVENTS),
    "opencode": frozenset({"PreToolUse"}),
    "antigravity": frozenset({"PreToolUse"}),
}

# The only Claude-vocabulary tool matcher the OpenCode/Antigravity adapters
# can translate. A manifest hook's `matcher` field names a CLAUDE tool (e.g.
# "Bash") -- it has no meaning to another CLI's own tool vocabulary, so the
# adapters below hardcode the one mapping that IS verified: OpenCode's
# `permission.ask` fires on `input.type === "bash"`; Antigravity's own
# `PreToolUse` grouping matches its shell tool by the literal string
# "run_command". A manifest matcher naming any other Claude tool has nothing
# for either adapter to translate to and is refused for that target, same as
# an unsupported event above.
GUARDRAIL_SHELL_MATCHER = "Bash"


def validate_permissions_manifest(data: Any, source: str | Path) -> dict[str, Any]:
    """Validate the private permissions manifest before anything is applied.

    A half-applied permission posture is worse than none: it can leave a
    runtime with prompts disabled and its guardrail hook missing. So this
    refuses the whole file rather than skipping a bad entry.
    """
    config = _mapping(data, source, "permissions manifest")
    _reject_unknown_keys(config, {"schema_version", "posture", "hooks"}, source, "permissions manifest")
    if type(config.get("schema_version")) is not int or config["schema_version"] != PERMISSIONS_SCHEMA_VERSION:
        _error(source, f"permissions manifest schema_version must be {PERMISSIONS_SCHEMA_VERSION}")

    posture = _mapping(config.get("posture", {}) or {}, source, "permissions manifest.posture")
    for cli, value in posture.items():
        if not isinstance(cli, str) or not cli.strip():
            _error(source, "permissions manifest.posture has a CLI key that is not a non-empty string")
        if value not in PERMISSION_POSTURES:
            _error(source, f"permissions manifest.posture.{cli} must be one of: {', '.join(sorted(PERMISSION_POSTURES))}")
        # Deliberately NOT checked here: whether `cli` has a verified renderer
        # for `value` (PERMISSION_RENDERERS). An unrenderable (cli, value)
        # pair is real instance policy for a dialect this engine cannot yet
        # translate, not malformed data -- the per-CLI dispatcher in
        # agent_sync.py decides, CLI by CLI: apply what it can, WARN and skip
        # what it can't, and still return success for the whole phase.

    hooks = config.get("hooks", []) or []
    if not isinstance(hooks, list):
        _error(source, "permissions manifest.hooks must be a list")
    for index, entry in enumerate(hooks):
        spec = _mapping(entry, source, f"permissions manifest.hooks[{index}]")
        _reject_unknown_keys(
            spec,
            {"name", "file", "runtime", "targets", "event", "matcher", "timeout", "description"},
            source,
            f"permissions manifest.hooks[{index}]",
        )
        name = spec.get("name")
        if not isinstance(name, str) or not ENTRY_NAME_RE.fullmatch(name):
            _error(source, f"permissions manifest.hooks[{index}].name must use letters, digits, '.', '_' or '-'")
        rel = spec.get("file")
        # Defense in depth: this path is joined onto the vault and the result
        # is copied into the user's runtime dir, so a traversal payload here
        # would write outside both. Refuse absolute paths and any '..'.
        if not isinstance(rel, str) or not rel or rel.startswith(("/", "\\")) or ".." in Path(rel).parts or Path(rel).is_absolute():
            _error(source, f"permissions manifest.hooks[{index}].file must be a relative path inside permissions/, without '..'")
        if spec.get("runtime", "node") != "node":
            _error(source, f"permissions manifest.hooks[{index}].runtime supports only 'node'")
        targets = spec.get("targets", [])
        if not isinstance(targets, list) or not targets or not all(t in PERMISSION_HOOK_TARGETS for t in targets):
            _error(source, f"permissions manifest.hooks[{index}].targets must be a non-empty list drawn from: {', '.join(sorted(PERMISSION_HOOK_TARGETS))}")
        if spec.get("event") not in PERMISSION_HOOK_EVENTS:
            _error(source, f"permissions manifest.hooks[{index}].event must be one of: {', '.join(sorted(PERMISSION_HOOK_EVENTS))}")
        matcher = spec.get("matcher")
        if matcher is not None and not isinstance(matcher, str):
            _error(source, f"permissions manifest.hooks[{index}].matcher must be a string")
        timeout = spec.get("timeout", 5)
        if type(timeout) is not int or timeout <= 0:
            _error(source, f"permissions manifest.hooks[{index}].timeout must be a positive integer")
    return config
