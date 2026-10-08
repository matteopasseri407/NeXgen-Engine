"""OpenCode adapter: posture in opencode.jsonc + native guardrail plugin.

OpenCode doesn't speak Claude's JSON: its guardrail hooks are JS callbacks loaded in-process,
not commands launched in a separate process. The static adapter at
agent-universal-layer/hooks/opencode-guardrail-plugin.mjs (already prepared, never touched
here) translates those callbacks into the same stdin/stdout JSON the guardrail body already
speaks for Claude -- one policy, three CLIs, never duplicated.

V2 contract (checked against the installed 2.0.24 binary, live, 2026-10-07): a plugin is the
default export `{ id, setup(ctx) }` of a DIRECTORY under `<config>/plugins/`, which OpenCode
loads by itself -- nothing is registered in the config. Registering a plugin FILE under
`plugins`, as this adapter did for 2.0.12, is refused at load ("configured plugin path must be
a directory"), and the V1 hooks (`permission.ask`, `tool.execute.before`) are never called:
the guardrail sat on disk, registered, and was never consulted. The hooks are now
`ctx.shell.hook("create.before")` (a veto that runs whatever the rules say) and
`ctx.permission.hook("evaluate")` (the answer to what the person would be asked).
Posture is ordered `permissions` rules with `action`/`resource`/`effect` (the V1 `permission`
object is migrated once, then dropped), and instructions load from the global `AGENTS.md`
scope file, never from the `instructions` array (accepted but unresolved).
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

from nexgen_core.jsonc import parse_jsonc, set_jsonc_top_level_value
from nexgen_core.paths import opencode_config_path
from nexgen_core.runtimes.base import EVENT_SINK_NAME, GUARDRAIL_CORE_NAME, GUARDRAIL_SIDECAR_NAME, GuardrailError, Runtime

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

#: Bypass as the engine renders it when the guardrail plugin is installed. The shell rule says
#: `ask` and the plugin answers it: `allow` for what the guardrail body permits (no prompt, as
#: bypass means), `deny` for the rest. The plugin also vetoes a denied command on its own
#: (`shell.hook create.before`), so this is not what makes it safe; it is what makes a plugin
#: that OpenCode stops calling visible: every command prompts, instead of nothing being checked
#: while the rule says `allow`.
_MEDIATED_BYPASS = [
    {"action": "edit", "resource": "*", "effect": "allow"},
    {"action": "shell", "resource": "*", "effect": "ask"},
]

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

_ADAPTER_NAME = "opencode-guardrail-plugin.mjs"  # the engine's source file; deployed as `index.mjs` of the plugin directory
_PLUGIN_DIRNAME = "nexgen-guardrail"
#: What earlier engines deployed into the config directory and registered by path. OpenCode 2.0.24 refuses a file
#: registration, so these were never loaded; they are taken back (and only these, by these names).
_LEGACY_ADAPTERS = ("opencode-guardrail-plugin.mjs", "nexgen-guardrail-plugin.mjs")
_LEGACY_FILES = _LEGACY_ADAPTERS + (GUARDRAIL_CORE_NAME, GUARDRAIL_SIDECAR_NAME)


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
            # OpenCode reads opencode.json with the same comment-tolerant parser as .jsonc.
            data = parse_jsonc(raw) if raw.strip() else {}
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
        # A mediated bypass writes shell as `ask` and lets the plugin answer (see
        # _mediated); on disk the rules are those of accept-edits, and only the sidecar
        # says the plugin answers for the person.
        if self.read_guardrail_sidecar(self.guardrail_sidecar(home)).get("autoAllow") and all(
            (r["action"], r["resource"], r["effect"]) in have for r in _MEDIATED_BYPASS
        ):
            return "bypass"
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
        mediated = posture == "bypass" and self._mediated(home)
        if mediated:
            desired = [dict(rule) for rule in _MEDIATED_BYPASS]
            for rule in rules:
                # The `allow` an earlier cycle wrote for this pair would keep the plugin unreachable.
                if isinstance(rule, dict) and (rule.get("action"), rule.get("resource"), rule.get("effect")) == (
                    "shell", "*", "allow",
                ):
                    rule["effect"] = "ask"
                    changed = True
        # The plugin answers for the person only under a mediated bypass; elsewhere the person
        # asked to be asked. Under bypass a broken guardrail must block rather than ask.
        self.set_guardrail_flags(self.guardrail_sidecar(home), autoAllow=mediated, strict=posture == "bypass")
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
        if raw.strip():
            updated = set_jsonc_top_level_value(raw, key, value)
        else:
            updated = json.dumps({key: value}, indent=2) + "\n"
        self.atomic_write(path, updated)

    @staticmethod
    def _raw_has_key(path: Path, key: str) -> bool:
        if not path.is_file():
            return False
        try:
            raw = path.read_text(encoding="utf-8")
            data = parse_jsonc(raw) if raw.strip() else {}
        except (OSError, ValueError):
            return False
        return isinstance(data, dict) and key in data

    def _delete_key(self, path: Path, key: str) -> None:
        from nexgen_core.jsonc import remove_jsonc_top_level_value

        raw = path.read_text(encoding="utf-8") if path.is_file() else "{}\n"
        self.backup(path)
        updated = remove_jsonc_top_level_value(raw, key) if raw.strip() else raw
        self.atomic_write(path, updated)

    def _plugin_dir(self, home: Path) -> Path:
        """Where the guardrail lives: a directory under OpenCode's own `plugins/`, which it loads by itself."""
        return self._config_path(home).parent / "plugins" / _PLUGIN_DIRNAME

    def guardrail_sidecar(self, home: Path) -> Path | None:
        return self._plugin_dir(home) / GUARDRAIL_SIDECAR_NAME

    def _mediated(self, home: Path) -> bool:
        """The guardrail plugin is deployed with everything it needs, so it can answer for the person."""
        plugin_dir = self._plugin_dir(home)
        sidecar = self.read_guardrail_sidecar(plugin_dir / GUARDRAIL_SIDECAR_NAME)
        return (
            bool(sidecar.get("hooks"))
            and (plugin_dir / "index.mjs").is_file()
            and (plugin_dir / GUARDRAIL_CORE_NAME).is_file()
        )

    def install_guardrail(self, home: Path, hook_source: Path, engine_hooks_dir: Path) -> str | None:
        from nexgen_core.runtimes.base import Runtime

        hook_name = hook_source.name
        if not Runtime._is_safe_hook_filename(hook_name):
            raise GuardrailError(f"opencode: unsafe guardrail filename {hook_name!r}")
        config_path = self._config_path(home)
        if not config_path.is_file():
            return None  # OpenCode never launched here: no guardrail to install
        plugin_dir = self._plugin_dir(home)

        # 1) Guardrail body (the policy, private to the Vault).
        body_dst = plugin_dir / "hooks" / hook_source.name
        body_changed = self.deploy_bytes(body_dst, hook_source.read_bytes())

        # 2) Engine's static adapter, as the entry file of the plugin directory.
        adapter_src = engine_hooks_dir / _ADAPTER_NAME
        if not adapter_src.is_file():
            raise GuardrailError(f"opencode: missing engine adapter ({adapter_src})")
        adapter_changed = self.deploy_bytes(plugin_dir / "index.mjs", adapter_src.read_bytes())
        adapter_changed |= self.deploy_guardrail_core(plugin_dir, engine_hooks_dir)

        # 3) Sidecar: which body to run and with what timeout, read fresh
        #    by the adapter on every call (no OpenCode restart needed to
        #    pick up a changed manifest).
        sidecar_changed = self.write_guardrail_sidecar(
            plugin_dir / GUARDRAIL_SIDECAR_NAME, body=body_dst, home=home,
        )

        # 4) Nothing is registered: OpenCode loads the directory by itself. What an earlier engine
        #    registered by file path (never loaded) and deployed into the config directory goes.
        retired = self._retire_legacy(config_path)

        if retired:
            return f"opencode: guardrail moved to the plugin directory {plugin_dir} (the old file registration never loaded)"
        if body_changed or adapter_changed or sidecar_changed:
            return f"opencode: guardrail body/adapter updated in {plugin_dir}"
        return None

    def _retire_legacy(self, config_path: Path) -> bool:
        """Takes back the file registrations and the files an earlier engine deployed for the guardrail.

        Only entries naming the engine's own adapter files and only those files, by name: every other
        plugin the user registered stays exactly as it was.
        """
        changed = False
        config = self._load(config_path)
        if config is not None:
            for key in ("plugins", "plugin"):
                values = config.get(key)
                if not isinstance(values, list):
                    continue
                kept = [p for p in values if not (isinstance(p, str) and p.rstrip("/").rsplit("/", 1)[-1] in _LEGACY_ADAPTERS)]
                if len(kept) != len(values):
                    self._write_key(config_path, key, kept)
                    changed = True
        config_dir = config_path.parent
        for name in _LEGACY_FILES:
            changed |= self.remove_deployed(config_dir / name)
        legacy_hooks = config_dir / "nexgen-guardrail-hooks"
        if legacy_hooks.is_dir():
            shutil.rmtree(legacy_hooks, ignore_errors=True)
            changed = True
        return changed

    def _register_plugin(self, config_path: Path, adapter_dst: Path) -> bool:
        # Still used by the event sink only (a V1-shaped plugin that OpenCode 2.0.24 does not load either; see CHANGELOG).
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

    def remove_event_sink(self, home: Path) -> str | None:
        config_path = self._config_path(home)
        config = self._load(config_path)
        changed = False
        if config is not None:
            import shlex as _shlex
            from pathlib import Path as _Path

            def _is_sink_plugin(p: object) -> bool:
                if not isinstance(p, str):
                    return False
                try:
                    parts = _shlex.split(p.strip(), posix=True)
                except ValueError:
                    parts = p.split()
                return bool(parts) and _Path(parts[0]).name == EVENT_SINK_NAME

            for key in ("plugins", "plugin"):
                values = config.get(key)
                if not isinstance(values, list):
                    continue
                kept = [p for p in values if not _is_sink_plugin(p)]
                if len(kept) != len(values):
                    self._write_key(config_path, key, kept)
                    changed = True
        removed = self.remove_deployed(config_path.parent / EVENT_SINK_NAME)
        if changed or removed:
            return f"opencode: event sink removed from {config_path} (no declared module needs it)"
        return None

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
