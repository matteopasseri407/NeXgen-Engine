"""Claude Code adapter: the PreToolUse guardrail hook in ~/.claude/settings.json.

Claude is the only CLI in this package that already natively speaks the
same JSON the guardrail body expects on stdin/stdout, so its adapter translates
nothing. It is there to fail closed: a body registered directly as the hook's command
fails open (see agent-universal-layer/hooks/claude-guardrail-adapter.mjs).

The permission mode (`permissions.defaultMode`) is Claude's own setting, written by
Claude when a person picks it: this adapter never writes it.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from nexgen_core.i18n import t
from nexgen_core.paths import claude_settings
from nexgen_core.runtimes.base import GUARDRAIL_SIDECAR_NAME, GuardrailError, Runtime

_ADAPTER_NAME = "claude-guardrail-adapter.mjs"
#: The body gets 5 s; the hook gets 10, so the adapter answers (and says why) before Claude kills it.
BODY_TIMEOUT_SECONDS = 5
_HOOK_TIMEOUT_SECONDS = 10

#: The tools whose commands the guardrail must see. Claude names a shell call by its tool, and on
#: Windows the shell is the `PowerShell` tool: a `Bash` matcher never matches it, so the hook stayed
#: registered, looked healthy to the doctor, and left every PowerShell command unchecked in the one
#: posture where it is the only brake (https://code.claude.com/docs/en/hooks, Windows tab).
_GUARDRAIL_MATCHER = "Bash|PowerShell"

#: Matchers that already reach the PowerShell tool: every tool, or both tools by name.
_GUARDRAIL_MATCHERS_COVERING = (None, "", "*", _GUARDRAIL_MATCHER)

#: The hook that re-grounds a reloaded or compacted session in the vault. This package ships the
#: script and its briefing text but deploys neither (that wiring is a manual step), so its
#: registration in Claude's own settings is the observable fact that Claude receives a briefing.
#: OpenClaw's plugin follows the same fact, so the two hosts brief the same sessions.
CHECKPOINT_HOOK_NAME = "claude-vault-checkpoint.mjs"


def adapter_name() -> str:
    """The file name of the fail-closed adapter, as every Claude surface names it."""
    return _ADAPTER_NAME


def plugin_hook_group(adapter: str) -> dict[str, Any]:
    """The `PreToolUse` group a native plugin contributes, in Claude Code's `hooks.json` shape.

    One definition with the registration in `settings.json`: the same matcher (both shell
    tools, see `_GUARDRAIL_MATCHER`) and the same timeout, so turning the plugin switch on
    cannot change what the guardrail does. The adapter is reached through
    `${CLAUDE_PLUGIN_ROOT}` because the installer owns where the directory lives.
    """
    return {
        "matcher": _GUARDRAIL_MATCHER,
        "hooks": [
            {
                "type": "command",
                "command": f'node "${{CLAUDE_PLUGIN_ROOT}}/hooks/{adapter}"',
                "timeout": _HOOK_TIMEOUT_SECONDS,
            }
        ],
    }


def _retire_guardrail_hook(entries: list[Any], commands: tuple[str, ...]) -> bool:
    """Takes our handler out of `PreToolUse`, and the group with it when it held nothing else.

    Only the exact command strings this engine has written are matched, so another tool's
    handler in the same group stays where it is. Returns True when something was removed.
    """
    kept: list[Any] = []
    removed = False
    for group in entries:
        hooks = group.get("hooks") if isinstance(group, dict) else None
        if not isinstance(hooks, list):
            kept.append(group)
            continue
        remaining = [h for h in hooks if not (isinstance(h, dict) and h.get("command") in commands)]
        if len(remaining) == len(hooks):
            kept.append(group)
            continue
        removed = True
        if remaining:
            group["hooks"] = remaining
            kept.append(group)
    if removed:
        entries[:] = kept
    return removed



def _register_guardrail_hook(entries: list[Any], command: str, legacy_command: str) -> str:
    """Put the adapter under `PreToolUse`, repairing an older registration in place.

    Returns "added" for a new group, "updated" when an existing handler or matcher was repaired
    (or a duplicate merged away), and "" when the registration already matched the spec. The
    matcher belongs to the spec: a group left at `Bash` by an older engine is repaired instead of
    counted as "already registered" (and never duplicated beside the repaired one). Only one
    group keeps our command: Claude runs every matching handler once per command, so a second
    copy runs the body twice, doubles the audit count and spends the hook budget twice.
    """
    # Match on the full spec, not the command alone: an entry with our command but a stale timeout
    # or matcher would otherwise be kept forever, while a mere quoting difference would duplicate
    # the group and fire the guardrail twice. The timeout is the adapter's, longer than the body's,
    # so the adapter always answers before Claude would cut it off (a timeout lets the command run).
    wanted = {"type": "command", "command": command, "timeout": _HOOK_TIMEOUT_SECONDS}
    kept: list[Any] = []
    kept_ours = False
    updated = False
    for group in entries:
        hooks = group.get("hooks") if isinstance(group, dict) else None
        ours = (
            [h for h in hooks if isinstance(h, dict) and h.get("command") in (command, legacy_command)]
            if isinstance(hooks, list) else []
        )
        if not ours:
            kept.append(group)
            continue
        if kept_ours:
            # A duplicate of the group we already repaired: drop our handlers, and the group
            # itself when it held nothing else. Another tool's handler in it stays where it is.
            group["hooks"] = [h for h in hooks if not any(h is mine for mine in ours)]
            if group["hooks"]:
                kept.append(group)
            updated = True
            continue
        kept_ours = True
        kept.append(group)
        if all(handler == wanted for handler in ours) and group.get("matcher") in _GUARDRAIL_MATCHERS_COVERING:
            continue
        for handler in ours:
            handler.clear()
            handler.update(wanted)
        if group.get("matcher") not in _GUARDRAIL_MATCHERS_COVERING:
            group["matcher"] = _GUARDRAIL_MATCHER
        updated = True
    if updated:
        entries[:] = kept
        return "updated"
    if kept_ours:
        return ""
    entries.append({"matcher": _GUARDRAIL_MATCHER, "hooks": [wanted]})
    return "added"


class ClaudeRuntime(Runtime):
    name = "claude"

    def _settings_path(self, home: Path) -> Path:
        return claude_settings(home)

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
        except (OSError, ValueError) as exc:
            # ValueError covers both malformed JSON and undecodable bytes
            # (UnicodeDecodeError): either way the file is unsafe to modify.
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

    def guardrail_sidecar(self, home: Path) -> Path | None:
        return home / ".claude" / GUARDRAIL_SIDECAR_NAME

    def install_guardrail(self, home: Path, hook_source: Path, engine_hooks_dir: Path) -> str | None:
        from nexgen_core.plugin_claude import installed as claude_runs_as_a_plugin

        if claude_runs_as_a_plugin(home):
            # This installation asked for the native plugin: the hook travels in its
            # `hooks.json`, so writing it here too would run the guardrail twice per command.
            return self._stand_down(home, hook_source)
        return self._install_guardrail_impl(home, hook_source, engine_hooks_dir)

    def _stand_down(self, home: Path, hook_source: Path) -> str | None:
        """Removes this engine's own registration, once, when the plugin carries the hook instead.

        Only the two command strings this engine writes are touched -- today's adapter and
        the body itself, as engines before the adapter registered it -- so a group a person
        or another tool wrote is left exactly as it is.
        """
        data = self._load_settings(home)
        if data is None:
            return None
        hooks = data.get("hooks")
        entries = hooks.get("PreToolUse") if isinstance(hooks, dict) else None
        if not isinstance(entries, list):
            return None
        claude_dir = home / ".claude"
        commands = (
            f'node "{claude_dir / _ADAPTER_NAME}"',
            f'node "{claude_dir / hook_source.name}"',
        )
        if not _retire_guardrail_hook(entries, commands):
            return None
        if not entries:
            # The event holds nothing else: an empty list is noise in a file a person reads.
            hooks.pop("PreToolUse", None)
        path = self._settings_path(home)
        self.backup(path)
        self.atomic_write(path, json.dumps(data, indent=2) + "\n")
        return t("claude: guardrail hook moved into the plugin (removed from {path})", path=path)

    def _install_guardrail_impl(self, home: Path, hook_source: Path, engine_hooks_dir: Path) -> str | None:
        from nexgen_core.runtimes.base import Runtime

        hook_name = hook_source.name
        if not Runtime._is_safe_hook_filename(hook_name):
            raise GuardrailError(f"claude: unsafe guardrail filename {hook_name!r}")
        data = self._load_settings(home)
        if data is None:
            return None
        claude_dir = home / ".claude"

        # The body (private policy) and the engine's adapter. Claude speaks the body's own
        # JSON, so the adapter translates nothing: it exists to fail CLOSED. A hook registered
        # directly fails open, because Claude treats any exit code but 2, and any timeout,
        # as a non-blocking error and runs the command anyway.
        body_dst = claude_dir / "nexgen-guardrail-hooks" / hook_source.name
        body_changed = self.deploy_bytes(body_dst, hook_source.read_bytes())
        adapter_src = engine_hooks_dir / _ADAPTER_NAME
        if not adapter_src.is_file():
            raise GuardrailError(f"claude: missing engine adapter ({adapter_src})")
        adapter_dst = claude_dir / _ADAPTER_NAME
        adapter_changed = self.deploy_bytes(adapter_dst, adapter_src.read_bytes())
        adapter_changed |= self.deploy_guardrail_core(claude_dir, engine_hooks_dir)
        sidecar_changed = self.write_guardrail_sidecar(
            claude_dir / GUARDRAIL_SIDECAR_NAME, body=body_dst, home=home, timeout=BODY_TIMEOUT_SECONDS,
        )

        command = f'node "{adapter_dst}"'
        # Before the adapter existed the body itself was the hook's command.
        legacy_command = f'node "{claude_dir / hook_source.name}"'
        hooks = data.setdefault("hooks", {})
        if not isinstance(hooks, dict):
            raise GuardrailError("claude: settings.hooks is not an object")
        entries = hooks.setdefault("PreToolUse", [])
        if not isinstance(entries, list):
            raise GuardrailError("claude: settings.hooks.PreToolUse is not a list")
        outcome = _register_guardrail_hook(entries, command, legacy_command)
        if outcome:
            path = self._settings_path(home)
            self.backup(path)
            self.atomic_write(path, json.dumps(data, indent=2) + "\n")
            if outcome == "added":
                return f"claude: guardrail hook registered in {path}"
            return f"claude: guardrail hook spec updated in {path}"
        if body_changed or adapter_changed or sidecar_changed:
            return f"claude: guardrail body/adapter updated in {claude_dir}"
        return None


def checkpoint_registered(home: Path) -> bool:
    """Whether Claude Code already runs the vault-briefing hook on `SessionStart`.

    Read-only, and tolerant by design: an unreadable or malformed settings file means "no
    briefing", which is the same answer as a machine where nobody wired the hook. The engine
    never writes this registration; it only lets OpenClaw's plugin follow the fact.
    """
    try:
        data = json.loads(claude_settings(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):  # nexgen: allow reason="an unreadable settings file means no briefing, which is the same answer as a machine that never wired the hook"
        return False
    hooks = data.get("hooks") if isinstance(data, dict) else None
    groups = hooks.get("SessionStart") if isinstance(hooks, dict) else None
    if not isinstance(groups, list):
        return False
    for group in groups:
        inner = group.get("hooks") if isinstance(group, dict) else None
        for item in inner if isinstance(inner, list) else []:
            command = item.get("command") if isinstance(item, dict) else None
            if isinstance(command, str) and CHECKPOINT_HOOK_NAME in command:
                return True
    return False
