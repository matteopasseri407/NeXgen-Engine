#!/usr/bin/env python3
"""Generator and renderer for MCP configurations across the 4 CLIs (Claude, Codex, OpenCode, Antigravity).

Contract order and rules:
1. Canonical manifest (mcp/manifest.yaml) as the single source of truth.
2. Resolution of stdio commands (command + expanded args) and HTTP servers with env-ref tokens.
3. Native support for the 4 CLIs: Claude, Codex (TOML), OpenCode, Antigravity (bridge).
4. Additive preservation of live servers absent from the manifest, except
   those listed in `retired_servers`: that's the explicit, cross-CLI removal
   mechanism, and it always wins over additive preservation.
5. Atomic writes with safety backups and strict error handling.
"""
from __future__ import annotations

import contextlib
import os
import platform
import sys
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.config import expand_inline_templates, expand_placeholders, load_mcp_manifest
from nexgen_core.paths import (
    opencode_config_path,
    resolve_engine_root,
    resolve_home,
    resolve_vault_data,
)

from nexgen_core.mcp_render import IS_WINDOWS


class McpRenderer:
    """Renders the MCP configuration for each supported CLI."""

    def __init__(
        self,
        vault_data: Path | None = None,
        engine_root: Path | None = None,
        home: Path | None = None,
    ) -> None:
        self.home = resolve_home(home)
        _v = resolve_vault_data(self.home, vault_data)
        self.vault_data = _v
        self.engine_root = resolve_engine_root(self.home, engine_root)

        self.manifest_path = self.vault_data / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
        self.path_placeholders = {
            "AGENT_ENGINE_ROOT": str(self.engine_root),
            "AGENT_VAULT_DATA": str(self.vault_data),
            "KNOWLEDGE_VAULT_PATH": str(Path(os.environ.get("KNOWLEDGE_VAULT_PATH") or self.vault_data)),
        }
        # Inline per-OS templating context: one manifest line can carry both
        # dialects instead of duplicating an entry into a `windows:` block
        # because a single arg holds a path.
        self.template_context = {
            "os": platform.system().lower(),
            "home": str(self.home),
            "vault": str(self.vault_data),
            "engine": str(self.engine_root),
        }

    def _expand_value(self, text: str) -> str:
        """One string value of a manifest entry, fully resolved: inline
        templates first (they select the branch), then ${VAR} placeholders
        inside whatever the branch chose. Unknown templates fail the render:
        literal `{{ }}` in a CLI config is the quiet wrong."""
        expanded = expand_inline_templates(text, self.template_context)
        return expand_placeholders(expanded, self.path_placeholders)

    def _normalize_windows_shim(self, exe: str) -> str:
        """Normalizes common executables on Windows (npx -> npx.cmd, python3 -> python)."""
        lower = exe.lower()
        if lower == "npx":
            return "npx.cmd"
        if lower == "python3":
            return "python"
        return exe

    def opencode_config_path(self) -> Path:
        """The OpenCode config file actually in effect, shared by every
        OpenCode path (renderer, guardrail adapter, inventory, doctor).

        One shared resolution (see ``nexgen_core.paths``): the release
        resolved the EXISTING file with priority jsonc > json > config.json,
        so an already-configured machine got updated on the file OpenCode
        actually reads, without creating a second one next to it. Two
        copies of this precedence already diverged once, which is how
        renderer, guardrail and doctor ended up able to operate on
        different files -- hence the single public accessor.
        """
        return opencode_config_path(self.home)

    def retired_server_names(self) -> set[str]:
        """The names of retired connectors: the explicit removal mechanism.

        `load_mcp_manifest` already excludes these names from the active
        servers, but the three CLIs that additively preserve existing live
        servers (Claude, Antigravity, OpenCode) would never drop them on
        their own: a retired connector must disappear from every rendered
        configuration, not merely be absent from new ones. Removing it is
        not a failure and must not be reported as one.
        """
        if not self.manifest_path.is_file():
            return set()
        data = load_mcp_manifest(self.manifest_path)
        return set(data.get("retired_servers", set()))

    def load_resolved_servers(self, cli_target: str) -> dict[str, dict[str, Any]]:
        """Loads the MCP servers resolved and filtered for a specific CLI."""
        if not self.manifest_path.is_file():
            return {}

        data = load_mcp_manifest(self.manifest_path)
        raw_servers = data.get("servers", {})
        resolved: dict[str, dict[str, Any]] = {}

        for name, srv in raw_servers.items():
            # Lazy contract: `tier: core` is always mounted; anything else
            # (absent tier or `tier: optional`) is mounted only when the
            # entry opts in with `enabled: true`. A registered-but-inert
            # server costs nothing at bootstrap and is never a problem.
            tier = str(srv.get("tier", "")).strip().lower()
            if tier != "core" and not srv.get("enabled", False):
                continue

            # Lazy routing: a server declared `lazy: true` is served by the
            # lazy-mcp proxy, not mounted directly in the CLIs listed in
            # `lazy_targets` (default: all four). CLIs outside the list keep
            # it direct (e.g. Claude, which defers schemas natively).
            if srv.get("lazy"):
                lazy_targets = srv.get("lazy_targets") or ["claude", "codex", "antigravity", "opencode"]
                if cli_target in lazy_targets:
                    continue

            targets = srv.get("targets", ["claude", "codex", "antigravity", "opencode"])
            if cli_target not in targets:
                continue

            # require_env check
            req_env = srv.get("require_env")
            if req_env:
                env_val = os.environ.get(req_env)
                if not env_val:
                    continue

            # Windows override
            entry = dict(srv)
            # `deps` is the provisioning contract, resolved at spawn by the
            # waiter; it must never reach the CLIs' native configs.
            entry.pop("deps", None)
            if IS_WINDOWS and "windows" in entry:
                win_override = entry.pop("windows")
                if isinstance(win_override, dict):
                    entry.update(win_override)

            # command and args resolution
            cmd = entry.get("command") or entry.get("cmd")
            args = entry.get("args", [])
            if isinstance(cmd, list):
                resolved_cmd = [self._expand_value(str(c)) for c in cmd]
                if IS_WINDOWS and resolved_cmd:
                    resolved_cmd[0] = self._normalize_windows_shim(resolved_cmd[0])
                entry["command"] = resolved_cmd[0] if resolved_cmd else ""
                entry["args"] = resolved_cmd[1:]
            elif isinstance(cmd, str):
                expanded_cmd = self._expand_value(cmd)
                if IS_WINDOWS:
                    expanded_cmd = self._normalize_windows_shim(expanded_cmd)
                entry["command"] = expanded_cmd
                if isinstance(args, list):
                    entry["args"] = [self._expand_value(str(a)) for a in args]
                else:
                    entry["args"] = []

            # URL resolution
            if entry.get("url"):
                entry["url"] = self._expand_value(str(entry["url"]))

            # env resolution
            if "env" in entry and isinstance(entry["env"], dict):
                entry["env"] = {
                    k: self._expand_value(str(v))
                    for k, v in entry["env"].items()
                }

            resolved[name] = entry

        return resolved

    def _drop_unmounted(self, mcp_servers: dict, mounted: dict, cli_target: str = "") -> None:
        """Lazy contract on the config side: a server declared in the manifest
        but not mounted for this CLI must not linger in a previous render.
        Exceptions, all deliberate:
        - servers OUTSIDE the manifest are never touched (additive rule);
        - env-gated servers that WOULD mount for this CLI (`require_env` and
          core/enabled and not lazy-routed away) stay on disk: the recurring
          guard runs without the shell environment, and deleting them there
          would make the doctor report them missing twice an hour, forever."""
        if not self.manifest_path.is_file():
            return
        data = load_mcp_manifest(self.manifest_path)
        for name, srv in data.get("servers", {}).items():
            if name in mounted:
                continue
            tier = str(srv.get("tier", "")).strip().lower()
            lazy_targets = srv.get("lazy_targets") or ["claude", "codex", "antigravity", "opencode"]
            routed_away = bool(srv.get("lazy")) and cli_target in lazy_targets
            would_mount = (tier == "core" or srv.get("enabled", False)) and not routed_away
            if srv.get("require_env") and would_mount:
                continue
            mcp_servers.pop(name, None)

    def list_lazy_servers(self, cli_target: str) -> list[str]:
        """Registered-but-inert servers for a CLI: known to exist, not mounted.

        The lazy contract's inventory side: an optional server without
        `enabled: true` is a choice, so it is listed, never reported as a
        problem.
        """
        if not self.manifest_path.is_file():
            return []
        data = load_mcp_manifest(self.manifest_path)
        out: list[str] = []
        for name, srv in data.get("servers", {}).items():
            tier = str(srv.get("tier", "")).strip().lower()
            if tier == "core" or srv.get("enabled", False):
                continue
            targets = srv.get("targets", ["claude", "codex", "antigravity", "opencode"])
            if cli_target in targets:
                out.append(name)
        return sorted(out)

    def render_claude(self, write: bool = False) -> tuple[bool, str]:
        """Generates the MCP configuration for Claude Code (~/.claude.json)."""
        from nexgen_core.mcp_render import claude
        return claude.render(self, write=write)

    def render_antigravity(self, write: bool = False) -> tuple[bool, str]:
        """Generates Antigravity's MCP configuration and fans it out to its consumers."""
        from nexgen_core.mcp_render import antigravity
        return antigravity.render(self, write=write)

    def render_opencode(self, write: bool = False) -> tuple[bool, str]:
        """Generates native OpenCode 2 MCP config and migrates flat V1 entries."""
        from nexgen_core.mcp_render import opencode
        return opencode.render(self, write=write)

    def render_codex(self, write: bool = False) -> tuple[bool, str]:
        """Generates Codex's native MCP configuration (~/.codex/config.toml)."""
        from nexgen_core.mcp_render import codex
        return codex.render(self, write=write)

    def render_all(self, write: bool = False) -> dict[str, bool]:
        """Renders for all 4 CLIs."""
        results: dict[str, bool] = {}
        ok_claude, _ = self.render_claude(write=write)
        ok_agy, _ = self.render_antigravity(write=write)
        ok_opencode, _ = self.render_opencode(write=write)
        ok_codex, _ = self.render_codex(write=write)
        results["claude"] = ok_claude
        results["antigravity"] = ok_agy
        results["opencode"] = ok_opencode
        results["codex"] = ok_codex
        return results

    def _backup_and_write(self, path: Path, content: str) -> None:
        """Makes a .bak-<timestamp> backup and writes the new content atomically.

        A config that already matches is left completely untouched: the guard
        cycle runs twice an hour, and rewriting a byte-identical file every
        cycle changes mtimes and piles up backups for nothing. Mechanics in
        `nexgen_core.files`; the last-3 rotation stays this writer's policy.
        """
        from nexgen_core.files import write_text_if_changed

        # Rendered configs may carry bearer tokens in env blocks: keep the
        # historical 0600 on POSIX (Windows has no equivalent bit here).
        if write_text_if_changed(path, content, keep=3) and not IS_WINDOWS:
            with contextlib.suppress(OSError):
                os.chmod(path, 0o600)
