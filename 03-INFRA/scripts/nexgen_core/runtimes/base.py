"""The single contract: one adapter per CLI, one single boundary to add a fifth.

The v1 release wrote the permission mode and the guardrail hook for four CLIs
inside agent_sync.py (~767 lines) with five separate rendering maps all in the
same file. Adding a CLI meant touching all of them. Here each CLI is a file
that implements this contract: adding one means adding a file.

An adapter registers the guardrail hook and nothing else. The permission mode
(bypass, accept edits, ask) is each app's own setting, written by the app: the
engine stopped translating a private `posture` into those keys, so a choice made
in an app is no longer reverted by the next guard cycle.
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


class GuardrailError(NexgenError):
    """An anomaly that prevents writing safely: malformed user config, a
    path that escapes the permitted folder, an unexpected shape in a key
    this engine owns.

    Deliberately distinct from "CLI not installed", which is normal and
    expressed with a plain None -- this one is for the anomaly that must never
    be allowed to pass silently, because otherwise a guardrail could be left
    half registered on disk while the doctor showed a healthy machine.
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

        return backup_file(path, tag="permissions", keep=3)

    @staticmethod
    def _is_safe_hook_filename(name: str) -> bool:
        import re

        return name not in (".", "..") and bool(re.fullmatch(r"[A-Za-z0-9._-]+", name))

    @staticmethod
    def atomic_write(path: Path, text: str) -> None:
        """Write-then-rename: a crash mid-write must never leave a
        truncated config that the CLI can no longer read on its next
        launch."""
        from nexgen_core.files import atomic_write_text

        atomic_write_text(path, text)

    @staticmethod
    def _needs_deploy(dst: Path, body: bytes) -> bool:
        return not (dst.exists() and dst.read_bytes() == body)

    @staticmethod
    def deploy_bytes(dst: Path, body: bytes) -> bool:
        """Copies `body` to `dst` only if different (idempotence: a guard
        that runs every few minutes must not rewrite an identical file on
        every cycle). Returns True if it wrote.

        Written atomically: a hook adapter cut off halfway is a script that
        does not parse, and a hook that fails to start is one the CLI treats
        as a non-blocking error: the guardrail would be off until the next cycle.
        """
        from nexgen_core.files import atomic_write_bytes

        if not Runtime._needs_deploy(dst, body):
            return False
        atomic_write_bytes(dst, body)
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

    def _build_sidecar_content(
        self,
        existing: dict,
        body: Path,
        home: Path,
        timeout: int,
        strict: bool | None,
        auto_allow: bool | None,
    ) -> str:
        return json.dumps(
            {
                "hooks": [{"file": str(body), "timeout": timeout}],
                "strict": bool(existing.get("strict")) if strict is None else strict,
                "autoAllow": bool(existing.get("autoAllow")) if auto_allow is None else auto_allow,
                "auditFile": str(self.guardrail_audit_file(home, self.name)),
            },
            indent=2,
        ) + "\n"

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
        value the sidecar already has: an adapter may set them in a later step (OpenCode's
        mediated shell rule does), and the next guardrail install must not undo that."""
        existing = self.read_guardrail_sidecar(path)
        content = self._build_sidecar_content(existing, body, home, timeout, strict, auto_allow)
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
