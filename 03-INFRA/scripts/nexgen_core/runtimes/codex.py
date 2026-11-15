"""Codex adapter: the PreToolUse guardrail hook in ~/.codex/hooks.json, and its trust state.

Codex runs a non-managed hook only after a person has reviewed and accepted that exact hook
definition in `/hooks`; it stores the acceptance as a hash under `[hooks.state]` in its own
config.toml and skips a new or changed hook until then
(developers.openai.com/codex/hooks, "Review and trust hooks"). So this adapter writes the hook
and then tells the truth about whether Codex will run it: the registration says the engine did
its part, `guardrail_trust` says whether Codex accepted it. Writing `trusted_hash` here would be
consent nobody gave -- there is no such code, and the `--dangerously-bypass-hook-trust` flag is
never passed by the engine.

A trusted hash already sitting at the slot this adapter writes is not evidence of anything: this
machine still carries one for a hook that was removed from the same index. The engine therefore
records the declaration it asked for at install time and counts only a hash that CHANGED
afterwards as an acceptance (`_remember_trust`).

The hook is registered under the matcher `Bash`: the body judges shell command lines, and
running it on `apply_patch` would hand it a patch envelope to read as a command. `approval_policy`
and `sandbox_mode` are Codex's own settings, written by Codex when a person picks Approvals or a
sandbox: this adapter never writes them.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tomllib
from pathlib import Path
from typing import Any

from nexgen_core.paths import codex_home
from nexgen_core.runtimes.base import GUARDRAIL_SIDECAR_NAME, GuardrailError, Runtime

_ADAPTER_NAME = "codex-guardrail-adapter.mjs"
#: The event we register in hooks.json, and the label Codex uses for it in `[hooks.state]`.
_HOOKS_EVENT = "PreToolUse"
_HOOK_STATE_EVENT = "pre_tool_use"
#: The one tool whose calls the guardrail body can read: Codex reports shell commands as `Bash`.
_GUARDRAIL_MATCHER = "Bash"
#: The body gets 5 s; the hook gets 10, so the adapter answers (and says why) before Codex gives up
#: and lets the tool run.
_BODY_TIMEOUT_SECONDS = 5
_HOOK_TIMEOUT_SECONDS = 10

#: What `guardrail_trust` can answer, once the hook is registered.
TRUSTED = "trusted"
UNTRUSTED = "untrusted"
STALE = "stale"


def _hooks_path(home: Path) -> Path:
    """Codex's user-level hook file, the one place this adapter writes its registration."""
    return codex_home(home) / "hooks.json"


def _trust_record_path(home: Path) -> Path:
    """Where the engine remembers which declaration it asked Codex to accept (machine state)."""
    from nexgen_core.paths import resolve_state_dir

    return resolve_state_dir(home) / "guardrail" / "codex-trust.json"


def _read_object(path: Path) -> dict:
    """The JSON object at `path`, or {} when it is absent or unusable. Read-only callers only."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # nexgen: allow reason="an unreadable record only means the engine has no baseline, which the doctor reports as not trusted; it must not raise inside a read"
        return {}
    return data if isinstance(data, dict) else {}


def _load_hooks(path: Path) -> dict:
    """The hook file as a dict, empty when absent, with `hooks` and `PreToolUse` present.

    Raises GuardrailError when the file exists but a half-write would break it: invalid JSON, a
    root that is not an object, or a `hooks`/`PreToolUse` shape this adapter does not own. A key
    that is `null` is read as absent, which is what it means here.
    """
    if not path.is_file():
        return {"hooks": {_HOOKS_EVENT: []}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GuardrailError(f"codex: {path} is not valid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise GuardrailError(f"codex: the root of {path} is not an object")
    hooks = data.get("hooks")
    if hooks is None:
        data["hooks"] = hooks = {}
    if not isinstance(hooks, dict):
        raise GuardrailError(f"codex: {path}: hooks must be an object")
    entries = hooks.get(_HOOKS_EVENT)
    if entries is None:
        hooks[_HOOKS_EVENT] = entries = []
    if not isinstance(entries, list):
        raise GuardrailError(f"codex: {path}: hooks.{_HOOKS_EVENT} must be a list")
    return data


def _register_guardrail_hook(entries: list[Any], command: str) -> str:
    """Put our adapter's handler under PreToolUse, leaving every other hook exactly as it was.

    Returns "added", "updated" or "" when the registration already matches the spec. The hook is
    found by its command, never by its position: hooks.json also holds hand-written hooks (this
    machine has three), and a group index says nothing about who wrote what. Only one group keeps
    our command, because Codex runs every matching hook and a second copy would consult the body
    twice per command.
    """
    wanted = {"type": "command", "command": command, "timeout": _HOOK_TIMEOUT_SECONDS}
    kept: list[Any] = []
    kept_ours = False
    updated = False
    for group in entries:
        hooks = group.get("hooks") if isinstance(group, dict) else None
        ours = (
            [handler for handler in hooks if isinstance(handler, dict) and handler.get("command") == command]
            if isinstance(hooks, list) else []
        )
        if not ours:
            kept.append(group)
            continue
        if kept_ours:
            # A duplicate of the group we already repaired: drop our handlers, and the group
            # itself when it held nothing else. Another tool's handler in it stays where it is.
            hooks[:] = [handler for handler in hooks if not any(handler is mine for mine in ours)]
            if hooks:
                kept.append(group)
            updated = True
            continue
        kept_ours = True
        kept.append(group)
        if len(ours) > 1:
            hooks[:] = [handler for handler in hooks if handler is ours[0] or not any(handler is mine for mine in ours[1:])]
            updated = True
        if ours[0] != wanted or group.get("matcher") != _GUARDRAIL_MATCHER:
            ours[0].clear()
            ours[0].update(wanted)
            group["matcher"] = _GUARDRAIL_MATCHER
            updated = True
    if updated:
        entries[:] = kept
        return "updated"
    if kept_ours:
        return ""
    entries.append({"matcher": _GUARDRAIL_MATCHER, "hooks": [wanted]})
    return "added"


def _our_slot(entries: list[Any], command: str) -> tuple[int, int, dict] | None:
    """The (group, handler) position of our adapter and the group it sits in, or None."""
    for group_index, group in enumerate(entries):
        hooks = group.get("hooks") if isinstance(group, dict) else None
        for handler_index, handler in enumerate(hooks if isinstance(hooks, list) else []):
            if isinstance(handler, dict) and handler.get("command") == command:
                return group_index, handler_index, group
    return None


def _definition_fingerprint(group: dict) -> str:
    """A stable digest of one declaration, so the engine can tell "same hook" from "changed hook"."""
    canonical = json.dumps(group, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _trusted_hash(home: Path, group_index: int, handler_index: int) -> str | None:
    """The hash Codex recorded for the hook at this slot, or None.

    Codex keeps it in `[hooks.state]` of its own config.toml, under the key
    `<hooks.json path>:<event>:<group index>:<handler index>`. Read only: the engine reports what
    Codex decided, it never writes the decision.
    """
    from nexgen_core.paths import codex_config

    path = codex_config(home)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # nexgen: allow reason="an unreadable Codex config means no recorded trust, which the doctor reports as not trusted rather than guessing; the file itself is Codex's to repair"
        return None
    hooks = data.get("hooks") if isinstance(data.get("hooks"), dict) else {}
    state = hooks.get("state") if isinstance(hooks.get("state"), dict) else {}
    for key, entry in state.items():
        # The path in the key is a Windows drive path, so the four fields are split from the right.
        parts = str(key).rsplit(":", 3)
        if len(parts) != 4 or not isinstance(entry, dict):
            continue
        source, event, group, handler = parts
        if (event, group, handler) != (_HOOK_STATE_EVENT, str(group_index), str(handler_index)):
            continue
        if os.path.normcase(os.path.abspath(source)) != os.path.normcase(os.path.abspath(_hooks_path(home))):
            continue
        trusted = entry.get("trusted_hash")
        return trusted if isinstance(trusted, str) else None
    return None


def guardrail_trust(home: Path) -> str | None:
    """What Codex's own record says about the guardrail hook registered for this home.

    None when the hook is not registered at all (nothing for anybody to accept), TRUSTED when
    Codex accepted the declaration now on disk, UNTRUSTED when it never did, and STALE when the
    hash on disk still belongs to an earlier declaration. The three answers come from comparing
    the hash Codex wrote with the one it had when the engine last wrote a declaration: a hash the
    engine never saw change is not evidence, because a removed hook leaves its hash behind at the
    same index.
    """
    entries = _load_hooks(_hooks_path(home))["hooks"][_HOOKS_EVENT]
    command = _adapter_command(home)
    slot = _our_slot(entries, command)
    if slot is None:
        return None
    group_index, handler_index, group = slot
    trusted = _trusted_hash(home, group_index, handler_index)
    record = _read_object(_trust_record_path(home))
    if trusted is None or not record:
        return UNTRUSTED
    if record.get("definition") != _definition_fingerprint(group):
        return STALE
    if trusted != record.get("trustedHash"):
        return TRUSTED
    # The same hash the engine saw when it wrote this declaration. That is a review Codex still
    # asks for -- of a declaration it had accepted before ("stale") or of one nobody ever
    # accepted and whose slot simply kept an older hook's hash ("untrusted").
    return STALE if record.get("carried") else UNTRUSTED


def _adapter_command(home: Path) -> str:
    """The exact `command` string registered for this adapter; the hook is found by it."""
    return f'node "{codex_home(home) / _ADAPTER_NAME}"'


class CodexRuntime(Runtime):
    name = "codex"

    def is_installed(self, home: Path) -> bool:
        del home
        # config.toml is NOT a valid signal: this same layer's MCP renderer
        # creates it from scratch on every cycle even on a machine where
        # Codex was never installed. Only the binary on the PATH is a
        # footprint that belongs exclusively to the product.
        return bool(shutil.which("codex"))

    def guardrail_sidecar(self, home: Path) -> Path | None:
        return codex_home(home) / GUARDRAIL_SIDECAR_NAME

    def install_guardrail(self, home: Path, hook_source: Path, engine_hooks_dir: Path) -> str | None:
        if not self._is_safe_hook_filename(hook_source.name):
            raise GuardrailError(f"codex: unsafe guardrail filename {hook_source.name!r}")
        codex_dir = codex_home(home)

        # The body (private policy) and the engine's adapter, which is what turns an "ask" into a
        # refusal and every crash into a deny, because Codex lets a tool run when a hook fails.
        body_dst = codex_dir / "nexgen-guardrail-hooks" / hook_source.name
        body_changed = self.deploy_bytes(body_dst, hook_source.read_bytes())
        adapter_src = engine_hooks_dir / _ADAPTER_NAME
        if not adapter_src.is_file():
            raise GuardrailError(f"codex: missing engine adapter ({adapter_src})")
        adapter_changed = self.deploy_bytes(codex_dir / _ADAPTER_NAME, adapter_src.read_bytes())
        adapter_changed |= self.deploy_guardrail_core(codex_dir, engine_hooks_dir)
        sidecar_changed = self.write_guardrail_sidecar(
            codex_dir / GUARDRAIL_SIDECAR_NAME,
            body=body_dst,
            home=home,
            timeout=_BODY_TIMEOUT_SECONDS,
            strict=True,
        )

        hooks_path = _hooks_path(home)
        data = _load_hooks(hooks_path)
        entries = data["hooks"][_HOOKS_EVENT]
        outcome = _register_guardrail_hook(entries, _adapter_command(home))
        if outcome:
            self.backup(hooks_path)
            self.atomic_write(hooks_path, json.dumps(data, indent=2) + "\n")

        slot = _our_slot(entries, _adapter_command(home))
        if slot is not None:
            group_index, handler_index, group = slot
            _remember_trust(home, group, _trusted_hash(home, group_index, handler_index))

        if outcome == "added":
            return (
                f"codex: guardrail hook registered in {hooks_path}; Codex skips it until you accept it: "
                "run /hooks inside Codex and trust the NeXgen guardrail hook"
            )
        if outcome == "updated":
            return f"codex: guardrail hook updated in {hooks_path}; open /hooks inside Codex to accept the change"
        if body_changed or adapter_changed or sidecar_changed:
            return f"codex: guardrail body/adapter updated in {codex_dir}"
        return None


def _remember_trust(home: Path, group: dict, trusted: str | None) -> None:
    """Record the declaration the engine is asking Codex to accept, and where it started from.

    Written only when the declaration changes. Refreshing the record on every cycle would adopt
    the hash of an acceptance as the baseline, and the next cycle would count the hook a person
    had just accepted as unaccepted again -- the same mistake, in the opposite direction.

    `carried` says whether the hash now on disk is the acceptance of the declaration being
    replaced: that is what tells "Codex wants a fresh review" apart from "nobody ever accepted
    this, and the slot still carries a removed hook's hash".
    """
    from nexgen_core.files import atomic_write_text

    fingerprint = _definition_fingerprint(group)
    path = _trust_record_path(home)
    previous = _read_object(path)
    if previous.get("definition") == fingerprint:
        return
    record = {
        "definition": fingerprint,
        "trustedHash": trusted,
        "carried": bool(previous) and trusted is not None and trusted != previous.get("trustedHash"),
    }
    atomic_write_text(path, json.dumps(record, indent=2) + "\n")
