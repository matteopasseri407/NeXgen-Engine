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

import os
import shutil
import time
from abc import ABC, abstractmethod
from pathlib import Path

#: Neutral vocabulary of the three posture levels this engine knows about.
#: An adapter without a verified rendering for one of these values silently
#: ignores it (apply_posture returns None) -- guessing at an unverified
#: dialect already caused an incident in v1.
POSTURES = ("bypass", "accept-edits", "ask")


class GuardrailError(Exception):
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
    from Path.home() internally), so a test can point every method at a
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

    @abstractmethod
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

    @staticmethod
    def backup(path: Path) -> Path | None:
        """Timestamped copy of an EXISTING user config file, made BEFORE
        any write. Every incident that justified this package started with
        a config file overwritten with nothing to recover from. No backup
        for a file that doesn't exist yet -- there's nothing to preserve."""
        if not path.is_file():
            return None
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup_path = path.with_name(f"{path.name}.pre-permissions-{stamp}.bak")
        shutil.copy2(path, backup_path)
        return backup_path

    @staticmethod
    def atomic_write(path: Path, text: str) -> None:
        """Write-then-rename: a crash mid-write must never leave a
        truncated config that the CLI can no longer read on its next
        launch."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)

    @staticmethod
    def deploy_bytes(dst: Path, body: bytes) -> bool:
        """Copies `body` to `dst` only if different (idempotence: a guard
        that runs every few minutes must not rewrite an identical file on
        every cycle). Returns True if it wrote."""
        if dst.exists() and dst.read_bytes() == body:
            return False
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(body)
        return True
