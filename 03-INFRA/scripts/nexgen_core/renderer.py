#!/usr/bin/env python3
"""Generator and renderer for MCP configurations across the 5 CLIs (Claude, Codex, OpenCode, Antigravity, OpenClaw) plus the Claude Desktop app.

Contract order and rules:
1. Canonical manifest (mcp/manifest.yaml) as the single source of truth.
2. Resolution of stdio commands (command + expanded args) and HTTP servers with env-ref tokens.
3. Native support for the 5 CLIs: Claude, Codex (TOML), OpenCode, Antigravity (bridge), OpenClaw (JSON `mcp.servers`); plus Claude Desktop (default-deny allowlist, see mcp_render/claude_desktop).
4. Additive preservation of live servers absent from the manifest, with two
   exceptions that always win over it: an exact name in `retired_servers` (a
   user-authorized tombstone), and a name this engine itself declared at an
   earlier render and the manifest no longer declares (remembered in the
   machine-local MCP state, so a removal needs nobody's memory). Every other
   unknown name is preserved.
   (On Desktop, manifest-known but not allowlisted servers are additionally
   reported, never removed.)
5. Atomic writes with safety backups and strict error handling.
"""
from __future__ import annotations

import contextlib
import importlib
import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Any, Callable

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.config import expand_inline_templates, expand_placeholders, load_mcp_manifest  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.paths import (  # noqa: E402 - sys.path shim for cloned checkout
    opencode_config_path,
    resolve_engine_root,
    resolve_home,
    resolve_vault_data,
)

from nexgen_core.mcp_render import IS_WINDOWS  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.mcp_placement import DIRECT, GATEWAY, GATEWAY_CLI_ENV, place  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.mcp_retirement import remember_declared, retired_names  # noqa: E402 - sys.path shim for cloned checkout


class McpRenderer:
    """Renders the MCP configuration for each supported CLI."""

    def __init__(
        self,
        vault_data: Path | None = None,
        engine_root: Path | None = None,
        home: Path | None = None,
        which: Callable[[str], str | None] | None = None,
        is_windows: bool | None = None,
    ) -> None:
        self.home = resolve_home(home)
        _v = resolve_vault_data(self.home, vault_data)
        self.vault_data = _v
        self.engine_root = resolve_engine_root(self.home, engine_root)
        self.which = which if which is not None else shutil.which
        self.is_windows = IS_WINDOWS if is_windows is None else bool(is_windows)

        self.manifest_path = self.vault_data / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
        #: While not None, writes are collected here instead of performed
        #: (see :meth:`pending_changes`).
        self._pending: dict[Path, str] | None = None
        #: True during :meth:`render_all`: the memory of declared servers is
        #: written once, after every dialect has pruned with the old one.
        self._batching_renders = False
        #: Error text per target from the last :meth:`render_all`, for the
        #: callers that must say why a target failed (the guard does).
        self.last_errors: dict[str, str] = {}
        #: OpenClaw only: connectors the caller wants reached over the local MCP
        #: bridge instead of launched on this machine (`{name: {url, token_env}}`,
        #: see `nexgen_core.mcp_bridge.bridged_http_servers`). None, the default,
        #: renders every connector as before.
        self.openclaw_http_servers: dict[str, Any] | None = None
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
        """The names that must disappear from every rendered configuration.

        Resolution order, and nothing else:
        1. a name the manifest declares now is live, and is never retired;
        2. an exact name in `retired_servers`, or a trial that expired or was
           dropped, is retired: the explicit, user-authorized mechanism;
        3. a name this engine declared at an earlier real render and the
           manifest no longer declares is retired too, without a tombstone:
           the engine remembers what it wrote (`mcp_state`), so a removal
           reaches every CLI even when nobody wrote one down;
        4. any other unknown name is preserved additively: someone put it
           there by hand, and that is not the engine's to delete. A name kept
           alive some other way (a running trial, an `orphans_allowlist`
           entry) is preserved even if the memory knows it.

        Read-only by contract: the memory is written by a real render
        (:meth:`render_all` or one `render_<cli>(write=True)`), never by a
        check, a plan or a dry run. The decision itself lives in
        `nexgen_core.mcp_retirement`.
        """
        return retired_names(self.manifest_path, self.home)

    def _remember_declared_servers(self, write: bool) -> None:
        """Records what this render declared, so a later removal needs no tombstone.

        Only a real write counts: a dry run, :meth:`pending_changes` and every
        doctor check read the memory and leave it as they found it. A render
        that raised never reaches this call.
        """
        if write and not self.previewing and not self._batching_renders:
            remember_declared(self.manifest_path, self.home)

    def _resolve_command_and_args(self, entry: dict[str, Any]) -> None:
        cmd = entry.get("command") or entry.get("cmd")
        args = entry.get("args", [])
        if isinstance(cmd, list):
            resolved_cmd = [self._expand_value(str(c)) for c in cmd]
            if self.is_windows and resolved_cmd:
                resolved_cmd[0] = self._normalize_windows_shim(resolved_cmd[0])
            entry["command"] = resolved_cmd[0] if resolved_cmd else ""
            entry["args"] = resolved_cmd[1:]
        elif isinstance(cmd, str):
            expanded_cmd = self._expand_value(cmd)
            if self.is_windows:
                expanded_cmd = self._normalize_windows_shim(expanded_cmd)
            entry["command"] = expanded_cmd
            entry["args"] = [self._expand_value(str(a)) for a in args] if isinstance(args, list) else []

        if self.is_windows and entry.get("command") == "openclaw":
            from nexgen_core.megaphone import openclaw_launch

            found = self.which("openclaw")
            if found:
                launch = openclaw_launch(found, self.which)
                if launch:
                    entry["command"] = launch[0]
                    entry["args"] = launch[1:] + (entry.get("args") or [])

    def _resolve_entry(
        self,
        name: str,
        srv: dict[str, Any],
        cli_target: str,
        gateway: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Resolves one manifest entry for a CLI: None when its `require_env`
        gate is unsatisfied or its command empties out after expansion.

        Shared by every dialect: what Desktop launches for an allowlisted
        server is resolved exactly like what a CLI would launch, never a
        second implementation of the same expansion.
        """
        import logging

        req_env = srv.get("require_env")
        if req_env and not os.environ.get(req_env):
            return None

        # Windows override
        entry = dict(srv)
        # `deps` is the provisioning contract, resolved at spawn by the
        # waiter; it must never reach the CLIs' native configs.
        entry.pop("deps", None)
        if self.is_windows and "windows" in entry:
            win_override = entry.pop("windows")
            if isinstance(win_override, dict):
                entry.update(win_override)

        self._resolve_command_and_args(entry)

        if not str(entry.get("command") or "").strip() and not entry.get("url"):
            logging.getLogger(__name__).warning("MCP server %r has an empty command after expansion, skipped", name)
            return None

        # URL resolution
        if entry.get("url"):
            entry["url"] = self._expand_value(str(entry["url"]))

        # env resolution
        if "env" in entry and isinstance(entry["env"], dict):
            entry["env"] = {
                k: (
                    str(v)
                    if str(v).startswith("${") and str(v).endswith("}") and str(v)[2:-1] not in self.path_placeholders
                    else self._expand_value(str(v))
                )
                for k, v in entry["env"].items()
            }

        if name == GATEWAY:
            # The gateway serves exactly what the plan routes behind it for THIS CLI, so it has to know which.
            entry["env"] = {**(entry.get("env") if isinstance(entry.get("env"), dict) else {}), GATEWAY_CLI_ENV: cli_target}
        elif entry.get("tools_deny") or entry.get("tools_allow"):
            entry = self._trimmed_entry(name, entry, gateway, cli_target)

        return entry

    def _manifest_servers(self) -> dict[str, dict[str, Any]]:
        servers: dict[str, dict[str, Any]] = {}
        if self.manifest_path.is_file():
            servers = load_mcp_manifest(self.manifest_path).get("servers", {})
        try:
            from nexgen_core import mcp_trials

            servers = mcp_trials.overlay(servers)
        except Exception:  # nexgen: allow reason="trial state failure must not stop manifest rendering"
            pass
        from nexgen_core.mcp_state import apply_state_overrides

        return apply_state_overrides(servers, home=self.home)

    def manifest_server_names(self) -> list[str]:
        """Every server the manifest declares, in manifest order, resolvable here or not."""
        return self._manifest_server_names_impl()

    def _manifest_server_names_impl(self) -> list[str]:
        return list(self._manifest_servers())

    def load_resolved_servers(self, cli_target: str) -> dict[str, dict[str, Any]]:
        """Loads the MCP servers resolved and filtered for a specific CLI.

        `claude-desktop` answers here too, with its allowlist, so every
        caller (checks, plan, inventory) shares the one entry point instead
        of branching on the CLI name.
        """
        return self._load_resolved_servers_impl(cli_target)

    def _load_resolved_servers_impl(self, cli_target: str) -> dict[str, dict[str, Any]]:
        if cli_target == "claude-desktop":
            from nexgen_core.mcp_render.claude_desktop import allowed_desktop_servers

            return allowed_desktop_servers(self)
        raw_servers = self._manifest_servers()
        resolved: dict[str, dict[str, Any]] = {}

        for name, srv in raw_servers.items():
            # One rule for where a server lives (mcp_placement): this CLI mounts it directly, or it is served
            # by the gateway, or it is not here at all. Only the first is rendered into the CLI's config.
            if place(srv, cli_target).kind != DIRECT:
                continue

            entry = self._resolve_entry(name, srv, cli_target, raw_servers.get(GATEWAY))
            if entry is None:
                continue

            resolved[name] = entry

        return resolved

    def _trimmed_entry(self, name: str, entry: dict[str, Any], gateway: dict[str, Any] | None, cli_target: str) -> dict[str, Any]:
        """A directly mounted server that hides some of its tools is mounted through `mcp-trim.py`.

        Same filter for every CLI (the alternative is four dialects, one of which does not exist), same
        tool names, so permissions written against them still mean the same thing.
        """
        interpreter = (gateway or {}).get("command") or "python3"
        script = self.engine_root / "agent-universal-layer" / "mcp" / "mcp-trim.py"
        trimmed: dict[str, Any] = {
            "transport": "stdio",
            "command": self._normalize_windows_shim(str(interpreter)) if IS_WINDOWS else str(interpreter),
            "args": [str(script), name],
            "env": {"AGENT_VAULT_DATA": str(self.vault_data), GATEWAY_CLI_ENV: cli_target},
            "tools_deny": entry.get("tools_deny") or [],
        }
        for key in ("timeouts", "targets", "tier", "exposure", "enabled"):
            if key in entry:
                trimmed[key] = entry[key]
        if entry.get("tools_allow"):
            trimmed["tools_allow"] = entry["tools_allow"]
        return trimmed

    def unmounted_server_names(self, mounted: dict, cli_target: str) -> set[str]:
        """Names to remove, shared by JSON and TOML dialects.

        Lazy contract on the config side: a server declared in the manifest
        but not mounted for this CLI must not linger in a previous render.
        Exceptions, all deliberate:
        - servers OUTSIDE the manifest are never touched (additive rule);
        - env-gated servers that WOULD mount for this CLI (`require_env` and
          core/enabled and not lazy-routed away) stay on disk: the recurring
          guard runs without the shell environment, and deleting them there
          would make the doctor report them missing twice an hour, forever."""
        servers = self._manifest_servers()
        unmounted = set()
        for name, srv in servers.items():
            if name in mounted:
                continue
            would_mount = place(srv, cli_target).kind == DIRECT
            if srv.get("require_env") and would_mount:
                continue
            unmounted.add(name)
        return unmounted

    def env_gated_server_names(self, cli_target: str) -> set[str]:
        """Servers that would mount for this CLI but whose `require_env` is unset here.

        The guard runs without the shell environment, so for these this
        process can tell neither "expected" nor "stray": checks must not
        decide on them, or the same machine gets opposite verdicts from
        the guard and from the shell.
        """
        if cli_target == "claude-desktop":
            from nexgen_core.mcp_render.claude_desktop import gated_desktop_names
            return gated_desktop_names(self)
        return {
            name for name, srv in self._manifest_servers().items()
            if srv.get("require_env") and not os.environ.get(srv["require_env"])
            and place(srv, cli_target).kind == DIRECT
        }

    def _drop_unmounted(self, mcp_servers: dict, mounted: dict, cli_target: str = "") -> None:
        for name in self.unmounted_server_names(mounted, cli_target):
            if name == "lazy-mcp" and cli_target == "openclaw":
                from nexgen_core.mcp_render.openclaw import is_engine_lazy_mcp

                if not is_engine_lazy_mcp(mcp_servers.get("lazy-mcp")):
                    continue
            mcp_servers.pop(name, None)

    def list_lazy_servers(self, cli_target: str) -> list[str]:
        """Registered-but-inert servers for a CLI: known to exist, not mounted.

        The lazy contract's inventory side: an optional server without
        `enabled: true` is a choice, so it is listed, never reported as a
        problem.
        """
        out: list[str] = []
        for name, srv in self._manifest_servers().items():
            # "Known to exist, not mounted": neither core nor enabled, or switched off while optional.
            why = place(srv, cli_target).why
            if why.startswith("inert") or (why == "disabled" and str(srv.get("tier", "")).strip().lower() != "core"):
                out.append(name)
        return sorted(out)

    def _render_dialect(self, dialect: str, write: bool, **kwargs: Any) -> tuple[bool, str]:
        """Runs one CLI's dialect writer, then remembers what a real write declared."""
        module = importlib.import_module(f"nexgen_core.mcp_render.{dialect}")
        result = module.render(self, write=write, **kwargs)
        self._remember_declared_servers(write)
        return result

    def render_claude(self, write: bool = False) -> tuple[bool, str]:
        """Generates the MCP configuration for Claude Code (~/.claude.json)."""
        return self._render_dialect("claude", write)

    def render_antigravity(self, write: bool = False) -> tuple[bool, str]:
        """Generates Antigravity's MCP configuration and fans it out to its consumers."""
        return self._render_dialect("antigravity", write)

    def render_opencode(self, write: bool = False) -> tuple[bool, str]:
        """Generates native OpenCode 2 MCP config and migrates flat V1 entries."""
        return self._render_dialect("opencode", write)

    def render_codex(self, write: bool = False) -> tuple[bool, str]:
        """Generates Codex's native MCP configuration (~/.codex/config.toml)."""
        return self._render_dialect("codex", write)

    def render_claude_desktop(self, write: bool = False) -> tuple[bool, str]:
        """Generates the Claude Desktop app's MCP configuration (default-deny allowlist)."""
        return self._render_dialect("claude_desktop", write)

    def render_openclaw(
        self,
        write: bool = False,
        http_servers: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        """Writes the connectors into OpenClaw's own config (~/.openclaw/openclaw.json)."""
        return self._render_dialect("openclaw", write, http_servers=http_servers)

    def render_all(self, write: bool = False) -> dict[str, bool]:
        """Renders for all targets, each isolated: one corrupt live config must not stall the others.

        The memory of declared servers is written once, after the whole pass:
        a per-target write would forget a removed name before the remaining
        dialects had pruned it.
        """
        results: dict[str, bool] = {}
        self.last_errors = {}
        self._batching_renders = True
        try:
            for name, method in (
                ("claude", self.render_claude),
                ("claude-desktop", self.render_claude_desktop),
                ("antigravity", self.render_antigravity),
                ("opencode", self.render_opencode),
                ("codex", self.render_codex),
                ("openclaw", self.render_openclaw),
            ):
                try:
                    ok, _ = method(write=write)
                except Exception as exc:
                    # Keep the message: a bare "failed" tells the user nothing
                    # about which line of which config to fix.
                    ok = False
                    self.last_errors[name] = str(exc) or type(exc).__name__
                results[name] = ok
        finally:
            self._batching_renders = False
        self._remember_declared_servers(write)
        return results

    @property
    def previewing(self) -> bool:
        """True inside :meth:`pending_changes`: nothing may touch the disk."""
        return self._pending is not None

    def pending_changes(self, cli_target: str) -> dict[Path, str]:
        """The files `render_<cli>(write=True)` would change right now, with their new text.

        Nothing is written. It runs the real renderer with its writer
        swapped for a collector, so a diagnostic that asks "would apply
        change anything?" can never disagree with apply itself, which is
        what a second, hand-written comparison of the two would eventually
        do. Raises what the renderer raises (an unreadable config, a
        manifest it cannot resolve).
        """
        self._pending = {}
        try:
            getattr(self, f"render_{cli_target.replace('-', '_')}")(write=True)
            return dict(self._pending)
        finally:
            self._pending = None

    def _backup_and_write(self, path: Path, content: str) -> None:
        """Makes a .bak-<timestamp> backup and writes the new content atomically.

        A config that already matches is left completely untouched: the guard
        cycle runs twice an hour, and rewriting a byte-identical file every
        cycle changes mtimes and piles up backups for nothing. Mechanics in
        `nexgen_core.files`; the last-3 rotation stays this writer's policy.
        Committed content needs no local backup either (`skip_if_committed`):
        git already holds the previous version, and the copy would only add
        debris that dirties data repositories.
        """
        from nexgen_core.files import write_text_if_changed

        if self._pending is not None:
            try:
                unchanged = Path(path).read_text(encoding="utf-8") == content
            except (OSError, UnicodeDecodeError):
                unchanged = False
            if not unchanged:
                self._pending[Path(path)] = content
            return

        # Rendered configs may carry bearer tokens in env blocks: keep the
        # historical 0600 on POSIX (Windows has no equivalent bit here).
        if write_text_if_changed(path, content, keep=3, skip_if_committed=True) and not IS_WINDOWS:
            with contextlib.suppress(OSError):
                os.chmod(path, 0o600)
