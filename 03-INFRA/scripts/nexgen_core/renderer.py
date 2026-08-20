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
import json
import os
import platform
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.config import expand_placeholders, load_mcp_manifest
from nexgen_core.i18n import t
from nexgen_core.jsonc import parse_jsonc, set_jsonc_top_level_value
from nexgen_core.paths import resolve_engine_root, resolve_home, resolve_vault_data

IS_WINDOWS = platform.system() == "Windows"
MCP_REMOTE_PACKAGE = "mcp-remote@0.1.38"


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

    def _normalize_windows_shim(self, exe: str) -> str:
        """Normalizes common executables on Windows (npx -> npx.cmd, python3 -> python)."""
        lower = exe.lower()
        if lower == "npx":
            return "npx.cmd"
        if lower == "python3":
            return "python"
        return exe

    def _opencode_config_path(self) -> Path:
        """Returns OpenCode's native config path.

        OpenCode uses ``opencode.jsonc`` (comments/trailing commas). The
        release resolved the EXISTING file with priority jsonc > json >
        config.json, so an already-configured machine got updated on the
        file OpenCode actually reads, without creating a second one next to it.
        """
        xdg_dir = self.home / ".config" / "opencode"
        xdg_candidates = [xdg_dir / name for name in ("opencode.jsonc", "opencode.json", "config.json")]
        for candidate in xdg_candidates:
            if candidate.is_file():
                return candidate
        if not IS_WINDOWS:
            return xdg_candidates[0]
        appdata_dir = Path(os.environ.get("APPDATA") or (self.home / "AppData" / "Roaming")) / "opencode"
        appdata_candidates = [appdata_dir / name for name in ("opencode.jsonc", "opencode.json", "config.json")]
        for candidate in appdata_candidates:
            if candidate.is_file():
                return candidate
        return xdg_candidates[0]

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
            if IS_WINDOWS and "windows" in entry:
                win_override = entry.pop("windows")
                if isinstance(win_override, dict):
                    entry.update(win_override)

            # command and args resolution
            cmd = entry.get("command") or entry.get("cmd")
            args = entry.get("args", [])
            if isinstance(cmd, list):
                resolved_cmd = [expand_placeholders(str(c), self.path_placeholders) for c in cmd]
                if IS_WINDOWS and resolved_cmd:
                    resolved_cmd[0] = self._normalize_windows_shim(resolved_cmd[0])
                entry["command"] = resolved_cmd[0] if resolved_cmd else ""
                entry["args"] = resolved_cmd[1:]
            elif isinstance(cmd, str):
                expanded_cmd = expand_placeholders(cmd, self.path_placeholders)
                if IS_WINDOWS:
                    expanded_cmd = self._normalize_windows_shim(expanded_cmd)
                entry["command"] = expanded_cmd
                if isinstance(args, list):
                    entry["args"] = [expand_placeholders(str(a), self.path_placeholders) for a in args]
                else:
                    entry["args"] = []

            # URL resolution
            if entry.get("url"):
                entry["url"] = expand_placeholders(str(entry["url"]), self.path_placeholders)

            # env resolution
            if "env" in entry and isinstance(entry["env"], dict):
                entry["env"] = {
                    k: expand_placeholders(str(v), self.path_placeholders)
                    for k, v in entry["env"].items()
                }

            resolved[name] = entry

        return resolved

    def render_claude(self, write: bool = False) -> tuple[bool, str]:
        """Generates the MCP configuration for Claude Code (~/.claude.json)."""
        servers = self.load_resolved_servers("claude")
        cfg_file = self.home / ".claude.json"
        existing: dict[str, Any] = {}
        if cfg_file.is_file():
            try:
                existing = json.loads(cfg_file.read_text(encoding="utf-8"))
            except Exception as exc:
                raise ValueError(f"Could not parse {cfg_file}: invalid JSON ({exc})")

        mcp_servers = existing.get("mcpServers", {})
        for retired in self.retired_server_names():
            mcp_servers.pop(retired, None)
        for name, srv in servers.items():
            if srv.get("transport") == "http" or srv.get("url"):
                auth_env = srv.get("auth", {}).get("env") if isinstance(srv.get("auth"), dict) else None
                headers = {"Authorization": f"Bearer ${{{auth_env}}}"} if auth_env else {}
                mcp_servers[name] = {
                    "type": "http",
                    "url": srv["url"],
                    "headers": headers,
                }
            else:
                mcp_servers[name] = {
                    "type": "stdio",
                    "command": srv.get("command", ""),
                    "args": srv.get("args", []),
                    "env": srv.get("env", {}),
                }

        existing["mcpServers"] = mcp_servers
        if write:
            self._backup_and_write(cfg_file, json.dumps(existing, indent=2) + "\n")
        return True, t("Claude configuration updated")

    #: Antigravity reads the same configuration from three different paths,
    #: depending on how it's launched. There's only one real file and the
    #: other three reach it: writing one and hoping it's the right one means
    #: leaving two variants out of three with a stale configuration.
    ANTIGRAVITY_CONSUMER_DIRS = ("antigravity-cli", "antigravity-ide", "config")

    def render_antigravity(self, write: bool = False) -> tuple[bool, str]:
        """Generates Antigravity's MCP configuration and fans it out to its consumers."""
        servers = self.load_resolved_servers("antigravity")
        cfg_file = self.home / ".gemini" / "antigravity" / "mcp_config.json"
        existing: dict[str, Any] = {}
        if cfg_file.is_file():
            try:
                existing = json.loads(cfg_file.read_text(encoding="utf-8"))
            except Exception as exc:
                raise ValueError(f"Could not parse {cfg_file}: invalid JSON ({exc})")

        mcp_servers = existing.get("mcpServers", {})
        for retired in self.retired_server_names():
            mcp_servers.pop(retired, None)
        bridge_script = self.engine_root / "agent-universal-layer" / "mcp" / "mcp-http-bridge.mjs"

        for name, srv in servers.items():
            if srv.get("transport") == "http" or srv.get("url"):
                auth_env = srv.get("auth", {}).get("env") if isinstance(srv.get("auth"), dict) else ""
                node_cmd = "node.exe" if IS_WINDOWS else "node"
                mcp_servers[name] = {
                    "command": node_cmd,
                    "args": [str(bridge_script), srv["url"], auth_env, MCP_REMOTE_PACKAGE],
                    "env": srv.get("env", {}),
                }
            else:
                mcp_servers[name] = {
                    "command": srv.get("command", ""),
                    "args": srv.get("args", []),
                    "env": srv.get("env", {}),
                }

        existing["mcpServers"] = mcp_servers
        if write:
            self._backup_and_write(cfg_file, json.dumps(existing, indent=2) + "\n")
            self._fan_out_antigravity(cfg_file)
        return True, t("Antigravity configuration updated")

    def _fan_out_antigravity(self, canonical: Path) -> None:
        """Points every path Antigravity reads from at the canonical file.

        Where links can't be created (Windows without privileges) it copies
        instead: what matters is that no variant is left behind.
        """
        for directory in self.ANTIGRAVITY_CONSUMER_DIRS:
            target = self.home / ".gemini" / directory / "mcp_config.json"
            if target == canonical:
                continue
            try:
                if target.is_symlink() and target.resolve() == canonical.resolve():
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists() or target.is_symlink():
                    target.unlink()
                target.symlink_to(canonical)
            except OSError:
                with contextlib.suppress(OSError):
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(canonical, target)

    def render_opencode(self, write: bool = False) -> tuple[bool, str]:
        """Generates the MCP configuration for OpenCode (opencode.jsonc, JSONC-aware)."""
        servers = self.load_resolved_servers("opencode")
        cfg_file = self._opencode_config_path()
        existing: dict[str, Any] = {}
        raw_existing = ""
        if cfg_file.is_file():
            raw_existing = cfg_file.read_text(encoding="utf-8")
            try:
                existing = parse_jsonc(raw_existing) if cfg_file.suffix == ".jsonc" else json.loads(raw_existing)
            except Exception as exc:
                raise ValueError(f"Could not parse {cfg_file}: invalid JSON/JSONC ({exc})")

        mcp_servers = existing.get("mcp", {})
        for retired in self.retired_server_names():
            mcp_servers.pop(retired, None)
        for name, srv in servers.items():
            if srv.get("transport") == "http" or srv.get("url"):
                url_env = srv.get("url_env")
                url = f"{{env:{url_env}}}" if url_env else srv["url"]
                auth_env = srv.get("auth", {}).get("env") if isinstance(srv.get("auth"), dict) else ""
                headers = {"Authorization": f"Bearer {{env:{auth_env}}}"} if auth_env else {}
                mcp_servers[name] = {
                    "type": "remote",
                    "url": url,
                    "headers": headers,
                    "enabled": True,
                    "oauth": False,
                }
            else:
                cmd_list = [srv.get("command", "")] + list(srv.get("args", []))
                entry_dict: dict[str, Any] = {
                    "type": "local",
                    "command": cmd_list,
                    "enabled": True,
                }
                if srv.get("env"):
                    entry_dict["environment"] = srv["env"]
                timeouts = srv.get("timeouts", {})
                if isinstance(timeouts, dict) and timeouts.get("tool"):
                    try:
                        entry_dict["timeout"] = int(float(timeouts["tool"]) * 1000)
                    except (ValueError, TypeError):
                        pass
                mcp_servers[name] = entry_dict

        existing["mcp"] = mcp_servers
        if write:
            # JSONC-aware: preserves the existing file's comments instead of
            # overwriting it with plain JSON (which OpenCode wouldn't read).
            if cfg_file.suffix == ".jsonc" and raw_existing.strip():
                content = set_jsonc_top_level_value(raw_existing, "mcp", mcp_servers)
            else:
                # File missing or empty: no comments to preserve.
                content = json.dumps(existing, indent=2) + "\n"
            self._backup_and_write(cfg_file, content)
        return True, t("OpenCode configuration updated")

    def render_codex(self, write: bool = False) -> tuple[bool, str]:
        """Generates Codex's native MCP configuration (~/.codex/config.toml)."""
        servers = self.load_resolved_servers("codex")
        cfg_file = self.home / ".codex" / "config.toml"

        existing_lines: list[str] = []
        if cfg_file.is_file():
            try:
                raw = cfg_file.read_text(encoding="utf-8")
                # Preserves existing non-MCP sections (e.g. [model], general settings)
                in_mcp_section = False
                for line in raw.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("[mcp_servers."):
                        in_mcp_section = True
                        continue
                    elif stripped.startswith("[") and not stripped.startswith("[mcp_servers."):
                        in_mcp_section = False
                    if not in_mcp_section and not line.startswith("# NeXgen Engine"):
                        existing_lines.append(line)
            except OSError:
                existing_lines = []

        header = "# NeXgen Engine - Codex MCP configuration, auto-generated"
        lines: list[str] = []
        if existing_lines:
            cleaned_existing = "\n".join(existing_lines).strip()
            if cleaned_existing:
                lines.append(cleaned_existing)
                lines.append("")

        lines.append(header)
        lines.append("")
        for name, srv in servers.items():
            safe_name = name.replace("-", "_")
            lines.append(f"[mcp_servers.{safe_name}]")
            if srv.get("transport") == "http" or srv.get("url"):
                lines.append(f'url = "{srv["url"]}"')
                auth_env = srv.get("auth", {}).get("env") if isinstance(srv.get("auth"), dict) else None
                if auth_env:
                    lines.append(f'bearer_token_env_var = "{auth_env}"')
                timeouts = srv.get("timeouts", {})
                if isinstance(timeouts, dict):
                    if "startup" in timeouts:
                        lines.append(f'startup_timeout_sec = {float(timeouts["startup"])}')
                    if "tool" in timeouts:
                        lines.append(f'tool_timeout_sec = {float(timeouts["tool"])}')
            else:
                lines.append(f'command = "{srv.get("command", "")}"')
                args_json = json.dumps(srv.get("args", []))
                lines.append(f"args = {args_json}")
                env = srv.get("env", {})
                if env:
                    lines.append(f"[mcp_servers.{safe_name}.env]")
                    for k, v in env.items():
                        lines.append(f'{k} = "{v}"')
            lines.append("")

        content = "\n".join(lines).strip() + "\n"
        if write:
            self._backup_and_write(cfg_file, content)
        return True, t("Codex configuration updated")

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
        """Makes a .bak-<timestamp> backup and writes the new content atomically."""
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file():
            stamp = time.strftime("%Y%m%d-%H%M%S")
            bak = path.with_name(f"{path.name}.bak-{stamp}")
            shutil.copy2(path, bak)
            # Keep only the last 3 backups
            backs = sorted(path.parent.glob(f"{path.name}.bak-*"))
            for old in backs[:-3]:
                old.unlink(missing_ok=True)

        # Atomic write via a tempfile on the same filesystem
        temp_fd, temp_path = tempfile.mkstemp(dir=str(path.parent), prefix=f"{path.name}.tmp-")
        try:
            with open(temp_fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(temp_path, str(path))
            if not IS_WINDOWS:
                os.chmod(str(path), 0o600)
        except Exception:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            raise
