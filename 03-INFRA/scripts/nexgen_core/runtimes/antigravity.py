"""Antigravity adapter: posture in settings.json + PreToolUse hook in hooks.json.

Antigravity has a REAL command-line hook (unlike OpenCode's in-process
callback), but with its own JSON shape: the static adapter at
agent-universal-layer/hooks/antigravity-guardrail-adapter.mjs translates
{toolCall, workspacePaths, ...} into the same stdin/stdout the guardrail
body already speaks for Claude.
"""
from __future__ import annotations

import json
import platform
import shutil
from pathlib import Path
from typing import Any

from nexgen_core.runtimes.base import GuardrailError, Runtime

_IS_WINDOWS = platform.system() == "Windows"

#: Neutral vocabulary -> Antigravity keys (verified against the reference
#: documentation shipped inside the installed binary). Only 'bypass' is
#: verified: there's no clean accept-edits split, because toolPermission
#: (shell) has no equivalent for files. Writing ONLY toolPermission would
#: leave file edits still blocked -- a half-bypass that looks applied and
#: isn't.
_POSTURE_RENDER = {"bypass": {"toolPermission": "always-proceed", "artifactReviewPolicy": "always-proceed"}}

_ADAPTER_NAME = "antigravity-guardrail-adapter.mjs"


class AntigravityRuntime(Runtime):
    name = "antigravity"

    def _settings_path(self, home: Path) -> Path:
        return home / ".gemini" / "antigravity-cli" / "settings.json"

    def _hooks_path(self, home: Path) -> Path:
        return home / ".gemini" / "config" / "hooks.json"

    def is_installed(self, home: Path) -> bool:
        if shutil.which("agy"):
            return True
        names = ("agy.exe", "agy.cmd", "agy") if _IS_WINDOWS else ("agy",)
        if any((home / ".local" / "bin" / name).is_file() for name in names):
            return True
        # settings.json is written only by Antigravity itself on first
        # launch -- unlike mcp_config.json, which this layer creates from
        # scratch on every cycle regardless of whether it's installed.
        return self._settings_path(home).is_file()

    def _load_json(self, path: Path, *, label: str) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise GuardrailError(f"antigravity: {label} is not valid JSON ({exc})") from exc
        if not isinstance(data, dict):
            raise GuardrailError(f"antigravity: the root of {label} is not an object")
        return data

    def read_posture(self, home: Path) -> str | None:
        try:
            data = self._load_json(self._settings_path(home), label="settings.json")
        except GuardrailError:
            return None
        if not data:
            return None
        for posture, desired in _POSTURE_RENDER.items():
            if all(data.get(k) == v for k, v in desired.items()):
                return posture
        return None

    def apply_posture(self, home: Path, posture: str) -> str | None:
        desired = _POSTURE_RENDER.get(posture)
        if desired is None:
            return None
        path = self._settings_path(home)
        data = self._load_json(path, label="settings.json")
        if data is None:
            return None  # Antigravity never launched here: no posture to apply
        if all(data.get(k) == v for k, v in desired.items()):
            return None
        data.update(desired)
        self.backup(path)
        self.atomic_write(path, json.dumps(data, indent=2) + "\n")
        return f"antigravity: posture '{posture}' applied in {path}"

    def install_guardrail(self, home: Path, hook_source: Path, engine_hooks_dir: Path) -> str | None:
        if not self._settings_path(home).is_file():
            return None  # Antigravity never launched here: no guardrail to install
        hooks_path = self._hooks_path(home)
        adapter_dir = hooks_path.parent

        body_dst = adapter_dir / "nexgen-guardrail-hooks" / hook_source.name
        body_changed = self.deploy_bytes(body_dst, hook_source.read_bytes())

        adapter_src = engine_hooks_dir / _ADAPTER_NAME
        if not adapter_src.is_file():
            raise GuardrailError(f"antigravity: missing engine adapter ({adapter_src})")
        adapter_dst = adapter_dir / _ADAPTER_NAME
        adapter_changed = self.deploy_bytes(adapter_dst, adapter_src.read_bytes())

        sidecar_path = adapter_dir / "nexgen-guardrail.config.json"
        # Antigravity's external hook timeout kills the WHOLE command; the
        # adapter already applies its own per-body timeout via subprocess,
        # so the buffer here keeps the external kill from firing before the
        # internal one gets its chance.
        timeout = 5
        sidecar_content = json.dumps({"hooks": [{"file": str(body_dst), "timeout": timeout}]}, indent=2) + "\n"
        sidecar_changed = not sidecar_path.is_file() or sidecar_path.read_text(encoding="utf-8") != sidecar_content
        if sidecar_changed:
            self.atomic_write(sidecar_path, sidecar_content)

        desired_entry = {
            "enabled": True,
            "PreToolUse": [{
                "matcher": "run_command",
                "hooks": [{"type": "command", "command": f"node {adapter_dst.as_posix()}", "timeout": timeout + 5}],
            }],
        }
        current = self._load_json(hooks_path, label="hooks.json") or {}
        # Only its own key: any other hook -- the user's or another tool's
        # -- in this file stays exactly as it was.
        if current.get("nexgen-guardrail") == desired_entry:
            registered = False
        else:
            self.backup(hooks_path)
            updated = {**current, "nexgen-guardrail": desired_entry}
            self.atomic_write(hooks_path, json.dumps(updated, indent=2) + "\n")
            registered = True

        if registered:
            return f"antigravity: guardrail registered in {hooks_path}"
        if body_changed or adapter_changed or sidecar_changed:
            return f"antigravity: guardrail body/adapter updated in {adapter_dir}"
        return None

    def install_event_sink(self, home: Path, sink_source: Path) -> str | None:
        if not self._settings_path(home).is_file():
            return None
        hooks_path = self._hooks_path(home)
        adapter_dir = hooks_path.parent
        dst = adapter_dir / sink_source.name
        deployed = self.deploy_bytes(dst, sink_source.read_bytes())

        command_done = f"node {dst.as_posix()} on_done antigravity"
        command_step = f"node {dst.as_posix()} on_step antigravity"

        desired_entry = {
            "enabled": True,
            "Stop": [
                {"type": "command", "command": command_done, "timeout": 2},
            ],
            "PreToolUse": [{
                "matcher": "*",
                "hooks": [{"type": "command", "command": command_step, "timeout": 2}],
            }],
        }
        current = self._load_json(hooks_path, label="hooks.json") or {}
        if current.get("nexgen-event-sink") == desired_entry:
            registered = False
        else:
            self.backup(hooks_path)
            updated = {**current, "nexgen-event-sink": desired_entry}
            self.atomic_write(hooks_path, json.dumps(updated, indent=2) + "\n")
            registered = True

        if registered:
            return f"antigravity: event sink registered in {hooks_path}"
        if deployed:
            return f"antigravity: event sink updated in {dst}"
        return None
