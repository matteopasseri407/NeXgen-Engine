"""Claude Code adapter: posture in ~/.claude/settings.json + PreToolUse hook.

Claude is the only CLI in this package that already natively speaks the
same JSON the guardrail body expects on stdin/stdout: no intermediate
adapter is needed, the body gets registered directly as the hook's command.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from nexgen_core.runtimes.base import GuardrailError, Runtime

#: Neutral vocabulary -> value that Claude understands (permissions.defaultMode).
#: Verified in the field, not invented: the binary's exact spelling.
_POSTURE_TO_CLAUDE = {"bypass": "bypassPermissions", "accept-edits": "acceptEdits", "ask": "default"}
_CLAUDE_TO_POSTURE = {v: k for k, v in _POSTURE_TO_CLAUDE.items()}


class ClaudeRuntime(Runtime):
    name = "claude"

    def _settings_path(self, home: Path) -> Path:
        return home / ".claude" / "settings.json"

    def is_installed(self, home: Path) -> bool:
        if shutil.which("claude"):
            return True
        # settings.json is written only by Claude Code itself on first
        # launch -- unlike ~/.claude.json (MCP config), which this layer
        # rewrites on every cycle regardless of whether it's installed.
        return self._settings_path(home).is_file()

    def _load_settings(self, home: Path) -> dict[str, Any] | None:
        """None if the file doesn't exist (CLI never launched here: nothing
        to merge into). Raises GuardrailError if it exists but is unsafe to
        modify -- invalid JSON, a root that isn't an object, or a `hooks`
        shape that a half-write would break."""
        path = self._settings_path(home)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise GuardrailError(f"claude: {path} is not valid JSON ({exc})") from exc
        if not isinstance(data, dict):
            raise GuardrailError(f"claude: the root of {path} is not an object")
        hooks = data.get("hooks")
        if hooks is not None:
            if not isinstance(hooks, dict):
                raise GuardrailError(f"claude: {path}: hooks must be an object")
            for event, matchers in hooks.items():
                if not isinstance(matchers, list):
                    raise GuardrailError(f"claude: {path}: hooks.{event} must be a list")
                if any(not isinstance(m, dict) for m in matchers):
                    raise GuardrailError(f"claude: {path}: hooks.{event} contains a non-object entry")
        return data

    def read_posture(self, home: Path) -> str | None:
        try:
            data = self._load_settings(home)
        except GuardrailError:
            return None
        if not data:
            return None
        mode = (data.get("permissions") or {}).get("defaultMode")
        return _CLAUDE_TO_POSTURE.get(mode)

    def apply_posture(self, home: Path, posture: str) -> str | None:
        if posture not in _POSTURE_TO_CLAUDE:
            return None
        data = self._load_settings(home)
        if data is None:
            return None  # Claude never launched here: no posture to apply
        before = json.dumps(data, sort_keys=True)
        perms = data.setdefault("permissions", {})
        if not isinstance(perms, dict):
            raise GuardrailError("claude: settings.permissions is not an object")
        perms["defaultMode"] = _POSTURE_TO_CLAUDE[posture]
        if posture == "bypass":
            # Without this Claude blocks on an interactive confirmation
            # dialog at startup, which a background guard can't answer.
            data["skipDangerousModePermissionPrompt"] = True
        if json.dumps(data, sort_keys=True) == before:
            return None
        path = self._settings_path(home)
        self.backup(path)
        self.atomic_write(path, json.dumps(data, indent=2) + "\n")
        return f"claude: posture '{posture}' applied in {path}"

    def install_guardrail(self, home: Path, hook_source: Path, engine_hooks_dir: Path) -> str | None:
        del engine_hooks_dir  # Claude already speaks the body's native dialect
        data = self._load_settings(home)
        if data is None:
            return None
        claude_dir = home / ".claude"
        dst = claude_dir / hook_source.name
        deployed = self.deploy_bytes(dst, hook_source.read_bytes())

        command = f'node "{dst}"'
        hooks = data.setdefault("hooks", {})
        if not isinstance(hooks, dict):
            raise GuardrailError("claude: settings.hooks is not an object")
        entries = hooks.setdefault("PreToolUse", [])
        if not isinstance(entries, list):
            raise GuardrailError("claude: settings.hooks.PreToolUse is not a list")
        already_registered = any(
            h.get("command") == command
            for matcher in entries if isinstance(matcher, dict)
            for h in matcher.get("hooks", [])
        )
        if already_registered:
            return f"claude: guardrail body updated in {dst}" if deployed else None

        entries.append({
            "matcher": "Bash",
            "hooks": [{"type": "command", "command": command, "timeout": 5}],
        })
        path = self._settings_path(home)
        self.backup(path)
        self.atomic_write(path, json.dumps(data, indent=2) + "\n")
        return f"claude: guardrail hook registered in {path}"

    def install_event_sink(self, home: Path, sink_source: Path) -> str | None:
        data = self._load_settings(home)
        if data is None:
            return None
        claude_dir = home / ".claude"
        dst = claude_dir / sink_source.name
        deployed = self.deploy_bytes(dst, sink_source.read_bytes())

        command_done = f'node "{dst}" on_done claude'
        command_step = f'node "{dst}" on_step claude'

        hooks = data.setdefault("hooks", {})
        if not isinstance(hooks, dict):
            raise GuardrailError("claude: settings.hooks is not an object")

        # Stop hook (on_done)
        stop_entries = hooks.setdefault("Stop", [])
        if not isinstance(stop_entries, list):
            raise GuardrailError("claude: settings.hooks.Stop is not a list")
        already_registered_stop = any(
            h.get("command") == command_done
            for matcher in stop_entries if isinstance(matcher, dict)
            for h in matcher.get("hooks", [])
        )
        if not already_registered_stop:
            stop_entries.append({
                "hooks": [{"type": "command", "command": command_done, "timeout": 2}],
            })

        # PreToolUse hook (on_step)
        pre_tool_entries = hooks.setdefault("PreToolUse", [])
        if not isinstance(pre_tool_entries, list):
            raise GuardrailError("claude: settings.hooks.PreToolUse is not a list")
        already_registered_step = any(
            h.get("command") == command_step
            for matcher in pre_tool_entries if isinstance(matcher, dict)
            for h in matcher.get("hooks", [])
        )
        if not already_registered_step:
            pre_tool_entries.append({
                "matcher": "*",
                "hooks": [{"type": "command", "command": command_step, "timeout": 2}],
            })

        if already_registered_stop and already_registered_step:
            return f"claude: event sink updated in {dst}" if deployed else None

        path_settings = self._settings_path(home)
        self.backup(path_settings)
        self.atomic_write(path_settings, json.dumps(data, indent=2) + "\n")
        return f"claude: event sink registered in {path_settings}"
