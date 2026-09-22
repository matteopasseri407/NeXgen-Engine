"""OpenCode adapter: posture in opencode.jsonc + native guardrail plugin.

OpenCode doesn't speak Claude's JSON: its only hook with veto power is
`permission.ask`, a JS callback loaded in-process, not a command launched
in a separate process. The static adapter at
agent-universal-layer/hooks/opencode-guardrail-plugin.mjs (already
prepared, never touched here) translates that callback into the same
stdin/stdout JSON the guardrail body already speaks for Claude -- one
policy, three CLIs, never duplicated.

V2 contract (verified against the official V2 docs and the installed
2.0.12 binary, 2026-09-22): plugins register under the `plugins` key (the
V1 `plugin` key is still normalized at runtime but is no longer written),
posture is ordered `permissions` rules with `action`/`resource`/`effect`
(the V1 `permission` object is migrated once, then dropped), and
instructions load from the global `AGENTS.md` scope file, never from the
`instructions` array (accepted but unresolved).
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

from nexgen_core.jsonc import parse_jsonc, set_jsonc_top_level_value
from nexgen_core.paths import opencode_config_path
from nexgen_core.runtimes.base import GuardrailError, Runtime

_IS_WINDOWS = sys.platform == "win32"

#: Neutral vocabulary -> V2 `permissions` rules (verified against the V2
#: config docs and the installed 2.0.12 binary's `debug config` output,
#: which normalizes the legacy `permission` object into this shape).
#: Ordered rules: to never override an explicit user rule, the engine only
#: adds a rule for an (action, resource) pair the user hasn't ruled on.
#: The legacy `bash` dimension is `shell` in V2.
_POSTURE_RENDER = {
    "bypass": [
        {"action": "edit", "resource": "*", "effect": "allow"},
        {"action": "shell", "resource": "*", "effect": "allow"},
    ],
    "accept-edits": [
        {"action": "edit", "resource": "*", "effect": "allow"},
        {"action": "shell", "resource": "*", "effect": "ask"},
    ],
}

#: Legacy V1 dimensions and their V2 action names. Only these dimensions
#: are migrated: every other `permission.*` key (webfetch, doom_loop, ...)
#: stays whatever the user set it to -- unless the user set it inside the
#: legacy object, in which case it is carried over as an equivalent rule
#: rather than dropped.
_LEGACY_PERMISSION_ACTIONS = {
    "edit": "edit",
    "bash": "shell",
    "websearch": "websearch",
    "webfetch": "webfetch",
}

_ADAPTER_NAME = "opencode-guardrail-plugin.mjs"


class OpenCodeRuntime(Runtime):
    name = "opencode"

    def _bin_names(self) -> tuple[str, ...]:
        return ("opencode.exe", "opencode.cmd", "opencode") if _IS_WINDOWS else ("opencode",)

    def is_installed(self, home: Path) -> bool:
        if shutil.which("opencode"):
            return True
        # opencode.jsonc is NOT a valid signal: this layer's MCP renderer
        # creates it from scratch on every cycle even if OpenCode was never
        # launched. The ~/.opencode/bin folder belongs only to the official
        # installer.
        bin_dir = home / ".opencode" / "bin"
        return any((bin_dir / name).is_file() for name in self._bin_names())

    def _config_path(self, home: Path) -> Path:
        """The REAL config file, with the same jsonc > json > config.json
        precedence OpenCode itself uses to resolve it -- an already
        configured machine must be updated on the file it actually reads,
        never on a fresh copy next to it. One shared resolution with the
        MCP renderer (see ``nexgen_core.paths``): two copies already
        diverged once, which is how renderer, guardrail and doctor ended up
        able to operate on different files."""
        return opencode_config_path(home)

    def _load(self, path: Path) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        raw = path.read_text(encoding="utf-8")
        try:
            data = parse_jsonc(raw) if path.suffix == ".jsonc" else json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            raise GuardrailError(f"opencode: {path.name} is not valid JSON/JSONC ({exc})") from exc
        if not isinstance(data, dict):
            raise GuardrailError(f"opencode: the root of {path.name} is not an object")
        return data

    def read_posture(self, home: Path) -> str | None:
        try:
            data = self._load(self._config_path(home))
        except GuardrailError:
            return None
        if not data:
            return None
        rules = data.get("permissions")
        if not isinstance(rules, list):
            return None
        have = {(r.get("action"), r.get("resource"), r.get("effect")) for r in rules if isinstance(r, dict)}
        for posture, desired in _POSTURE_RENDER.items():
            if all((r["action"], r["resource"], r["effect"]) in have for r in desired):
                return posture
        return None

    def apply_posture(self, home: Path, posture: str) -> str | None:
        desired = _POSTURE_RENDER.get(posture)
        if desired is None:
            return None
        path = self._config_path(home)
        data = self._load(path)
        if data is None:
            return None  # OpenCode never launched here: no posture to apply
        changed = self._migrate_legacy_permission(data)
        rules = data.get("permissions", [])
        if rules is not None and not isinstance(rules, list):
            raise GuardrailError(f"opencode: {path.name}: 'permissions' is not an array")
        rules = list(rules or [])
        claimed = {(r.get("action"), r.get("resource")) for r in rules if isinstance(r, dict)}
        for rule in desired:
            if (rule["action"], rule["resource"]) in claimed:
                continue  # the user already ruled on this pair: theirs wins
            rules.append(dict(rule))
            claimed.add((rule["action"], rule["resource"]))
            changed = True
        if not changed:
            return None
        self._write_key(path, "permissions", rules)
        # The legacy object has been migrated above: leaving it next to the
        # native rules would mean two sources of truth for the same posture.
        # A second surgical write drops it so JSONC comments survive both.
        if self._raw_has_key(path, "permission"):
            self._delete_key(path, "permission")
        return f"opencode: posture '{posture}' applied in {path}"

    @staticmethod
    def _migrate_legacy_permission(data: dict[str, Any]) -> bool:
        """Folds a V1 `permission` object into equivalent V2 rules, in place.

        Returns True when it added anything. Unknown dimensions are carried
        over verbatim (action name = legacy key) instead of dropped: losing
        a user's `webfetch: allow` silently would be worse than a rule the
        user recognizes. Malformed values fail closed via GuardrailError at
        the call site, never by guessing.
        """
        legacy = data.get("permission")
        if legacy is None:
            return False
        if not isinstance(legacy, dict):
            raise GuardrailError("opencode: 'permission' is not an object")
        rules = data.get("permissions")
        if rules is None:
            rules = []
            data["permissions"] = rules
        if not isinstance(rules, list):
            raise GuardrailError("opencode: 'permissions' is not an array")
        claimed = {(r.get("action"), r.get("resource")) for r in rules if isinstance(r, dict)}
        changed = False
        for legacy_key, effect in legacy.items():
            if effect not in ("allow", "ask", "deny"):
                raise GuardrailError(f"opencode: 'permission.{legacy_key}' has an unknown effect {effect!r}")
            action = _LEGACY_PERMISSION_ACTIONS.get(legacy_key, legacy_key)
            if (action, "*") in claimed:
                continue
            rules.append({"action": action, "resource": "*", "effect": effect})
            claimed.add((action, "*"))
            changed = True
        return changed

    def _write_key(self, path: Path, key: str, value: Any) -> None:
        raw = path.read_text(encoding="utf-8") if path.is_file() else "{}\n"
        self.backup(path)
        if path.suffix == ".jsonc":
            updated = set_jsonc_top_level_value(raw, key, value)
        else:
            data = json.loads(raw) if raw.strip() else {}
            data[key] = value
            updated = json.dumps(data, indent=2) + "\n"
        self.atomic_write(path, updated)

    @staticmethod
    def _raw_has_key(path: Path, key: str) -> bool:
        if not path.is_file():
            return False
        try:
            raw = path.read_text(encoding="utf-8")
            data = parse_jsonc(raw) if path.suffix == ".jsonc" else json.loads(raw or "{}")
        except (OSError, ValueError):
            return False
        return isinstance(data, dict) and key in data

    def _delete_key(self, path: Path, key: str) -> None:
        from nexgen_core.jsonc import remove_jsonc_top_level_value

        raw = path.read_text(encoding="utf-8") if path.is_file() else "{}\n"
        self.backup(path)
        if path.suffix == ".jsonc":
            updated = remove_jsonc_top_level_value(raw, key)
        else:
            data = json.loads(raw) if raw.strip() else {}
            data.pop(key, None)
            updated = json.dumps(data, indent=2) + "\n"
        self.atomic_write(path, updated)

    def install_guardrail(self, home: Path, hook_source: Path, engine_hooks_dir: Path) -> str | None:
        config_path = self._config_path(home)
        if not config_path.is_file():
            return None  # OpenCode never launched here: no guardrail to install
        plugin_dir = config_path.parent

        # 1) Guardrail body (the policy, private to the Vault).
        body_dst = plugin_dir / "nexgen-guardrail-hooks" / hook_source.name
        body_changed = self.deploy_bytes(body_dst, hook_source.read_bytes())

        # 2) Engine's static adapter (translates permission.ask -> stdin/stdout).
        adapter_src = engine_hooks_dir / _ADAPTER_NAME
        if not adapter_src.is_file():
            raise GuardrailError(f"opencode: missing engine adapter ({adapter_src})")
        adapter_dst = plugin_dir / _ADAPTER_NAME
        adapter_changed = self.deploy_bytes(adapter_dst, adapter_src.read_bytes())

        # 3) Sidecar: which body to run and with what timeout, read fresh
        #    by the adapter on every call (no OpenCode restart needed to
        #    pick up a changed manifest).
        sidecar_path = plugin_dir / "nexgen-guardrail.config.json"
        sidecar_content = json.dumps({"hooks": [{"file": str(body_dst), "timeout": 5}]}, indent=2) + "\n"
        sidecar_changed = not sidecar_path.is_file() or sidecar_path.read_text(encoding="utf-8") != sidecar_content
        if sidecar_changed:
            self.atomic_write(sidecar_path, sidecar_content)

        # 4) Registration in the "plugin" array -- append and dedup, every
        #    other plugin the user has stays exactly as it was.
        plugin_registered = self._register_plugin(config_path, adapter_dst)

        if plugin_registered:
            return f"opencode: guardrail registered in {config_path}"
        if body_changed or adapter_changed or sidecar_changed:
            return f"opencode: guardrail body/adapter updated in {plugin_dir}"
        return None

    def _register_plugin(self, config_path: Path, adapter_dst: Path) -> bool:
        # V2 native key is `plugins`; the V1 `plugin` key is still honored
        # at runtime but no longer written. A machine carrying the legacy
        # key is migrated once: both lists merge, dedupe, land in `plugins`,
        # and the legacy key is dropped -- one registration, one place.
        config = self._load(config_path)
        if config is None:
            return False
        try:
            entry = adapter_dst.resolve().as_uri()
        except (OSError, ValueError) as exc:
            raise GuardrailError(f"opencode: could not resolve {adapter_dst} ({exc})") from exc
        merged: list[Any] = []
        for key in ("plugins", "plugin"):
            values = config.get(key, [])
            if values is None:
                continue
            if not isinstance(values, list):
                raise GuardrailError(f"opencode: {config_path.name}: {key!r} is not a list")
            for plugin in values:
                if plugin not in merged:
                    merged.append(plugin)
        if any(isinstance(p, str) and p.strip() == entry for p in merged):
            registered = True
        else:
            merged.append(entry)
            registered = False
        legacy_present = self._raw_has_key(config_path, "plugin")
        current = config.get("plugins")
        if registered and not legacy_present and current == merged:
            return False
        self._write_key(config_path, "plugins", merged)
        if legacy_present or self._raw_has_key(config_path, "plugin"):
            self._delete_key(config_path, "plugin")
        return True

    def install_event_sink(self, home: Path, sink_source: Path) -> str | None:
        config_path = self._config_path(home)
        if not config_path.is_file() and not shutil.which("opencode"):
            return None
        plugin_dir = config_path.parent
        dst = plugin_dir / sink_source.name
        deployed = self.deploy_bytes(dst, sink_source.read_bytes())
        plugin_registered = self._register_plugin(config_path, dst)
        if plugin_registered:
            return f"opencode: event sink plugin registered in {config_path}"
        if deployed:
            return f"opencode: event sink updated in {plugin_dir}"
        return None
