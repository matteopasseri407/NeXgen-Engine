"""The single contract: one adapter per CLI, one single boundary to add a fifth.

The v1 release applied posture + guardrail hook for four CLIs inside
agent_sync.py (~767 lines) with five separate rendering maps
(PERMISSION_RENDERERS, CODEX_POSTURE_RENDER, OPENCODE_POSTURE_RENDER,
ANTIGRAVITY_POSTURE_RENDER, PERMISSION_HOOK_TARGETS...) all in the same
file. Adding a CLI meant touching all of them. Here each CLI is a file that
implements this contract: adding one means adding a file.

Postures travel in NEUTRAL vocabulary (bypass / accept-edits / ask), never
in a specific CLI's dialect -- translation is each adapter's own internal
responsibility, never the caller's.
"""
from __future__ import annotations

import json

from abc import ABC, abstractmethod
from pathlib import Path

from nexgen_core.errors import NexgenError

#: The shared JavaScript core every guardrail adapter imports, and the sidecar that
#: tells an adapter which body to run. Both live next to the adapter in the CLI's config
#: directory (see agent-universal-layer/hooks/nexgen-guardrail-core.mjs).
GUARDRAIL_CORE_NAME = "nexgen-guardrail-core.mjs"
GUARDRAIL_SIDECAR_NAME = "nexgen-guardrail.config.json"

#: The event-sink hook's file name: how its registrations are recognised for removal. Nothing
#: else is ever removed, whatever else the user has registered on the same events.
EVENT_SINK_NAME = "nexgen-event-sink.mjs"

#: Neutral vocabulary of the three posture levels this engine knows about.
#: An adapter without a verified rendering for one of these values silently
#: ignores it (apply_posture returns None) -- guessing at an unverified
#: dialect already caused an incident in v1.
POSTURES = ("bypass", "accept-edits", "ask")


class GuardrailError(NexgenError):
    """An anomaly that prevents writing safely: malformed user config, a
    path that escapes the permitted folder, an unexpected shape in a key
    this engine owns.

    Deliberately distinct from "CLI not installed" or "posture not
    supported here", which are normal and expressed with a plain None --
    this one is for the anomaly that must never be allowed to pass
    silently, because otherwise a posture that removes prompts could reach
    disk without the guardrail that was supposed to accompany it.
    """


class Runtime(ABC):
    """A CLI adapter. `home` travels explicitly on every call (never read
    by reading the home directory itself), so a test can point every method at a
    tmp_path and the user's real home never gets touched by mistake.
    """

    name: str

    @abstractmethod
    def is_installed(self, home: Path) -> bool:
        """Is this CLI actually present on this machine?

        Must be inferred from the PRODUCT's footprint -- its binary on the
        PATH, or a file that only it writes on first launch -- never from
        the existence of a folder or config file that this very layer
        creates (the MCP renderer writes ~/.claude.json,
        ~/.codex/config.toml, opencode.jsonc, and mcp_config.json on every
        cycle, installed or not: none of these is a valid signal). This
        defect was found twice in 24 hours in the previous release.
        """

    @abstractmethod
    def read_posture(self, home: Path) -> str | None:
        """The posture in effect RIGHT NOW, in neutral vocabulary, or None
        if this CLI's config doesn't exist or doesn't express one."""

    @abstractmethod
    def apply_posture(self, home: Path, posture: str) -> str | None:
        """Translates `posture` (neutral vocabulary) into this CLI's dialect.

        Returns an action line if it wrote something, None if there was
        nothing to do -- already correct (idempotence), or this CLI has no
        verified rendering for that value (silent skip, never a guessed
        attempt)."""

    def rendered_postures(self) -> tuple[str, ...]:
        """Neutral postures this adapter can actually render.

        Lets the orchestrator tell "already correct" apart from "asked
        for something this CLI cannot do": the latter warns instead of
        passing silent. The maps live as module globals per adapter
        (each adapter names its own); this probes the known ones.
        """
        import sys as _sys

        module = _sys.modules.get(self.__module__)
        for attr in ("_POSTURE_RENDER", "_POSTURE_TO_CLAUDE"):
            render = getattr(module, attr, None) if module is not None else None
            if render is None:
                render = getattr(self, attr, None)
            if isinstance(render, dict):
                return tuple(render)
        return ()

    @abstractmethod
    def install_event_sink(self, home: Path, sink_source: Path) -> str | None:
        """Registers the universal event sink hook (IPC emitter for lifecycle events).
        Default implementation returns None if not supported by the runtime."""
        del home, sink_source
        return None

    def remove_event_sink(self, home: Path) -> str | None:
        """Takes back what `install_event_sink` registered, and nothing else.

        The hook starts a Node process on every tool call of every session to emit an event that
        only a voice cockpit listens for, so a machine without one pays for it on each step. The
        guard removes it from machines whose declared modules do not need it.
        Returns an action line when it changed something."""
        del home
        return None

    @staticmethod
    def strip_event_sink_hooks(hooks: dict, events: tuple[str, ...] = ("Stop", "PreToolUse")) -> bool:
        """Drops the sink's hook items from `{event: [{matcher?, hooks: [...]}]}`; True if it dropped any.

        A group left with no hooks goes with them, and an event left with no groups; groups that
        also hold someone else's hooks keep those exactly as they were.
        """
        changed = False
        for event in events:
            groups = hooks.get(event)
            if not isinstance(groups, list):
                continue
            kept = []
            for group in groups:
                inner = group.get("hooks") if isinstance(group, dict) else None
                if not isinstance(inner, list):
                    kept.append(group)
                    continue
                remaining = [h for h in inner if not (isinstance(h, dict) and EVENT_SINK_NAME in str(h.get("command", "")))]
                if len(remaining) == len(inner):
                    kept.append(group)
                    continue
                changed = True
                if remaining:
                    kept.append({**group, "hooks": remaining})
            if kept:
                hooks[event] = kept
            else:
                del hooks[event]
        return changed

    @staticmethod
    def remove_deployed(path: Path) -> bool:
        """Deletes a file the engine deployed; True if it was there."""
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        return True

    def install_guardrail(self, home: Path, hook_source: Path, engine_hooks_dir: Path) -> str | None:
        """Registers the pre-execution hook whose POLICY lives in
        `hook_source` (private Vault content, identical for every CLI).
        `engine_hooks_dir` is the engine's public folder with the thin
        adapters already prepared (agent-universal-layer/hooks/*.mjs) for
        CLIs whose native contract doesn't speak the same JSON as Claude;
        CLIs that don't need it ignore it.

        Returns an action line if it changed something, None if already in
        order or if this CLI has no verified guardrail hookup."""

    # ---- shared helpers, available to every adapter -------------------
    # Disk mechanics live in exactly one place (`nexgen_core.files`): every
    # incident that justified this package started with a config file
    # overwritten with nothing to recover from.

    @staticmethod
    def backup(path: Path) -> Path | None:
        """Timestamped copy of an EXISTING user config file, made BEFORE
        any write. No backup for a file that doesn't exist yet -- there's
        nothing to preserve."""
        from nexgen_core.files import backup_file

        return backup_file(path, tag="permissions")

    @staticmethod
    def atomic_write(path: Path, text: str) -> None:
        """Write-then-rename: a crash mid-write must never leave a
        truncated config that the CLI can no longer read on its next
        launch."""
        from nexgen_core.files import atomic_write_text

        atomic_write_text(path, text)

    @staticmethod
    def deploy_bytes(dst: Path, body: bytes) -> bool:
        """Copies `body` to `dst` only if different (idempotence: a guard
        that runs every few minutes must not rewrite an identical file on
        every cycle). Returns True if it wrote.

        Written atomically: a hook adapter cut off halfway is a script that
        does not parse, and a hook that fails to start is one the CLI treats
        as a non-blocking error: the guardrail would be off until the next cycle.
        """
        import os

        if dst.exists() and dst.read_bytes() == body:
            return False
        dst.parent.mkdir(parents=True, exist_ok=True)
        staging = dst.with_name(f"{dst.name}.nexgen-tmp")
        staging.write_bytes(body)
        os.replace(staging, dst)
        return True

    # ---- guardrail support shared by every adapter ---------------------

    @staticmethod
    def guardrail_audit_file(home: Path, cli: str) -> Path:
        """Where an adapter records that the guardrail was consulted (machine state)."""
        from nexgen_core.paths import resolve_state_dir

        return resolve_state_dir(home) / "guardrail" / f"{cli}.json"

    def deploy_guardrail_core(self, directory: Path, engine_hooks_dir: Path) -> bool:
        source = engine_hooks_dir / GUARDRAIL_CORE_NAME
        if not source.is_file():
            raise GuardrailError(f"{self.name}: missing engine guardrail core ({source})")
        return self.deploy_bytes(directory / GUARDRAIL_CORE_NAME, source.read_bytes())

    def write_guardrail_sidecar(
        self,
        path: Path,
        *,
        body: Path,
        home: Path,
        timeout: int = 5,
        strict: bool | None = None,
        auto_allow: bool | None = None,
    ) -> bool:
        """Writes the sidecar the adapter reads on every call. Flags that are not passed keep the
        value the sidecar already has: the posture (which decides them) is applied after the
        guardrail, in a separate call, and must not be undone by the next guardrail install."""
        existing = self.read_guardrail_sidecar(path)
        content = json.dumps(
            {
                "hooks": [{"file": str(body), "timeout": timeout}],
                "strict": bool(existing.get("strict")) if strict is None else strict,
                "autoAllow": bool(existing.get("autoAllow")) if auto_allow is None else auto_allow,
                "auditFile": str(self.guardrail_audit_file(home, self.name)),
            },
            indent=2,
        ) + "\n"
        if path.is_file() and path.read_text(encoding="utf-8") == content:
            return False
        self.atomic_write(path, content)
        return True

    @staticmethod
    def read_guardrail_sidecar(path: Path) -> dict:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def set_guardrail_flags(self, path: Path, **flags: bool) -> bool:
        """Changes only the named flags of an existing sidecar; no sidecar, no guardrail, nothing to do."""
        current = self.read_guardrail_sidecar(path)
        if not current:
            return False
        updated = {**current, **flags}
        if updated == current:
            return False
        self.atomic_write(path, json.dumps(updated, indent=2) + "\n")
        return True

    def guardrail_sidecar(self, home: Path) -> Path | None:
        """Where this CLI's adapter reads its sidecar, or None if this CLI has no guardrail hookup."""
        del home
        return None
