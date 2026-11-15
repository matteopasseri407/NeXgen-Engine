"""OpenCode adapter: the guardrail plugin + the mediated shell rule.

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
`ctx.permission.hook("evaluate")` (the answer to what the person would be asked), and an `ask`
rule on `shell` is what makes the second one reachable. Instructions load from the global
`AGENTS.md` scope file, never from the `instructions` array (accepted but unresolved).

The engine writes that one `shell` rule and the sidecar flags that let the plugin answer it,
and nothing else: the permission mode (what `edit` and the other actions do) is the app's own
setting, written by OpenCode.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

from nexgen_core.jsonc import parse_jsonc, set_jsonc_top_level_value
from nexgen_core.opencode_compat import supports_v2
from nexgen_core.paths import opencode_config_path
from nexgen_core.runtimes.base import GUARDRAIL_CORE_NAME, GUARDRAIL_SIDECAR_NAME, GuardrailError, Runtime

_IS_WINDOWS = sys.platform == "win32"

#: The one V2 `permissions` rule the engine writes (shape verified against the V2 config docs
#: and the installed binary's `debug config` output). The shell rule says `ask` so that
#: `ctx.permission.hook("evaluate")` is reached on every command -- OpenCode only consults the
#: permission hook for an action it is about to ask about -- and the plugin then answers it
#: (`allow` for what the guardrail body permits, `deny` for the rest). Every other rule is the
#: person's, including whatever `edit` does: that is the app's own setting now.
_MEDIATED_SHELL_RULE = {"action": "shell", "resource": "*", "effect": "ask"}


def _person_asks_to_be_prompted(existing: dict[str, Any] | None, sidecar: dict) -> bool:
    """True when the `ask` shell rule on disk is the person's own, not this engine's.

    The engine writes exactly the same rule to make the plugin reachable, so the text cannot
    say who wrote it; the sidecar can, because only a mediated install -- one that answers the
    prompt itself -- ever sets `autoAllow`. A person who wants to approve each command keeps it.
    """
    return (
        existing is not None
        and existing.get("effect") == "ask"
        and sidecar.get("autoAllow") is not True
    )

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

    def _apply_mediated_rules(self, home: Path) -> bool:
        """Puts the `shell: ask` rule and the sidecar flags the plugin needs to answer it.

        The guardrail plugin is only consulted for an action OpenCode is about to ask about:
        with a `shell: allow` rule it sat on disk, registered, and answered nothing, so no
        command was ever checked. `ask` makes every command reach the plugin, `autoAllow` lets
        it answer `allow` for what the body permits (so nothing prompts, which is how a host
        running without prompts behaves) and `strict` makes a broken guardrail block instead
        of asking. The legacy V1 `permission` object is folded into equivalent rules first, so
        the same pair never has two sources of truth.

        A person who chose `ask` keeps being asked: `autoAllow` is then left off, so the plugin
        only vetoes a denied command and the prompt still reaches them.

        Returns True when it changed the config or the sidecar.
        """
        if not supports_v2():
            # A V1 CLI cannot read ordered `permissions` rules and refuses the whole
            # config, so writing them would stop OpenCode from starting at all. The
            # guardrail still runs from the plugin and its sidecar.
            return False
        path = self._config_path(home)
        data = self._load(path)
        if data is None:
            return False  # OpenCode never launched here: no config to write into
        changed = self._migrate_legacy_permission(data)
        rules = data.get("permissions", [])
        if rules is not None and not isinstance(rules, list):
            raise GuardrailError(f"opencode: {path.name}: 'permissions' is not an array")
        rules = list(rules or [])
        pair = (_MEDIATED_SHELL_RULE["action"], _MEDIATED_SHELL_RULE["resource"])
        existing = next(
            (r for r in rules if isinstance(r, dict) and (r.get("action"), r.get("resource")) == pair),
            None,
        )
        person_asks = _person_asks_to_be_prompted(
            existing, self.read_guardrail_sidecar(self.guardrail_sidecar(home)),
        )
        if existing is None:
            rules.append(dict(_MEDIATED_SHELL_RULE))
            changed = True
        elif existing.get("effect") == "allow":
            # An `allow` left by an earlier engine keeps the plugin unreachable. Only that
            # value is upgraded: a person's `deny` on the same pair stays exactly as it is.
            existing["effect"] = "ask"
            changed = True
        if changed:
            self._write_key(path, "permissions", rules)
            # The legacy object has been migrated above: leaving it next to the native rules
            # would mean two sources of truth for the same command. A second surgical write
            # drops it so JSONC comments survive both.
            if self._raw_has_key(path, "permission"):
                self._delete_key(path, "permission")
        flags_changed = self.set_guardrail_flags(
            self.guardrail_sidecar(home), autoAllow=not person_asks, strict=True,
        )
        return changed or flags_changed

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

    def install_guardrail(self, home: Path, hook_source: Path, engine_hooks_dir: Path) -> str | None:
        return self._install_guardrail_impl(home, hook_source, engine_hooks_dir)

    def _install_guardrail_impl(self, home: Path, hook_source: Path, engine_hooks_dir: Path) -> str | None:
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

        # 4) The `shell: ask` rule and the flags that let the plugin answer it, written only
        #    after the body, the adapter and the sidecar all landed: a rule that removes a
        #    prompt must never reach disk without the guardrail that has to answer it.
        mediated_changed = self._apply_mediated_rules(home)

        # 5) Nothing is registered: OpenCode loads the directory by itself. What an earlier engine
        #    registered by file path (never loaded) and deployed into the config directory goes.
        retired = self._retire_legacy(config_path)

        if retired:
            return f"opencode: guardrail moved to the plugin directory {plugin_dir} (the old file registration never loaded)"
        if mediated_changed:
            return f"opencode: shell rule set to 'ask' and the plugin answers it, in {config_path}"
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
