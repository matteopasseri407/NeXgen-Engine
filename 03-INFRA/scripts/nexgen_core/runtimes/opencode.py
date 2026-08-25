"""OpenCode adapter: posture in opencode.jsonc + native guardrail plugin.

OpenCode doesn't speak Claude's JSON: its only hook with veto power is
`permission.ask`, a JS callback loaded in-process, not a command launched
in a separate process. The static adapter at
agent-universal-layer/hooks/opencode-guardrail-plugin.mjs (already
prepared, never touched here) translates that callback into the same
stdin/stdout JSON the guardrail body already speaks for Claude -- one
policy, three CLIs, never duplicated.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
from pathlib import Path
from typing import Any

from nexgen_core.jsonc import parse_jsonc, set_jsonc_top_level_value
from nexgen_core.runtimes.base import GuardrailError, Runtime

_IS_WINDOWS = platform.system() == "Windows"

#: Neutral vocabulary -> permission.{edit,bash} (verified against the
#: installed type definitions of @opencode-ai/sdk). Only these two
#: dimensions: the other permission.* keys (webfetch, doom_loop, ...) stay
#: whatever the user set them to.
_POSTURE_RENDER = {
    "bypass": {"edit": "allow", "bash": "allow"},
    "accept-edits": {"edit": "allow", "bash": "ask"},
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
        never on a fresh copy next to it."""
        candidates_dirs = [home / ".config" / "opencode"]
        if _IS_WINDOWS:
            appdata = Path(os.environ.get("APPDATA") or (home / "AppData" / "Roaming"))
            candidates_dirs.append(appdata / "opencode")
        for base in candidates_dirs:
            for name in ("opencode.jsonc", "opencode.json", "config.json"):
                candidate = base / name
                if candidate.is_file():
                    return candidate
        return candidates_dirs[0] / "opencode.jsonc"

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
        permission = data.get("permission")
        if not isinstance(permission, dict):
            return None
        for posture, desired in _POSTURE_RENDER.items():
            if all(permission.get(k) == v for k, v in desired.items()):
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
        current_permission = data.get("permission")
        if current_permission is not None and not isinstance(current_permission, dict):
            raise GuardrailError(f"opencode: {path.name}: 'permission' is not an object")
        permission = dict(current_permission or {})
        if all(permission.get(k) == v for k, v in desired.items()):
            return None
        permission.update(desired)
        self._write_key(path, "permission", permission)
        return f"opencode: posture '{posture}' applied in {path}"

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
        config = self._load(config_path)
        if config is None:
            return False
        plugins = config.get("plugin", [])
        if not isinstance(plugins, list):
            raise GuardrailError(f"opencode: {config_path.name}: 'plugin' is not a list")
        try:
            entry = adapter_dst.resolve().as_uri()
        except (OSError, ValueError) as exc:
            raise GuardrailError(f"opencode: could not resolve {adapter_dst} ({exc})") from exc
        if any(isinstance(p, str) and p.strip() == entry for p in plugins):
            return False
        self._write_key(config_path, "plugin", [*plugins, entry])
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
