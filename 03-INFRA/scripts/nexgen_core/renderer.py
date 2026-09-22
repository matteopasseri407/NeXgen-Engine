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
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.config import expand_inline_templates, expand_placeholders, load_mcp_manifest
from nexgen_core.i18n import t
from nexgen_core.jsonc import parse_jsonc, remove_jsonc_top_level_value, set_jsonc_top_level_value
from nexgen_core.paths import opencode_config_path, resolve_engine_root, resolve_home, resolve_vault_data

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

    def manifest_server_names(self) -> set[str]:
        """Every server declared in the manifest, mounted or not."""
        if not self.manifest_path.is_file():
            return set()
        return set(load_mcp_manifest(self.manifest_path).get("servers", {}))

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
        self._drop_unmounted(mcp_servers, servers, cli_target="claude")
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
        self._drop_unmounted(mcp_servers, servers, cli_target="antigravity")
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

    @staticmethod
    def _opencode_timeouts(timeouts: Any) -> dict[str, int]:
        """Map canonical seconds to OpenCode 2's millisecond timeout names."""
        if not isinstance(timeouts, dict):
            return {}
        mapped = {}
        for source, target in (("startup", "startup"), ("tool", "execution")):
            try:
                millis = int(float(timeouts[source]) * 1000)
                if millis > 0:
                    mapped[target] = millis
            except (KeyError, ValueError, TypeError, OverflowError):
                pass
        return mapped

    def render_opencode(self, write: bool = False) -> tuple[bool, str]:
        """Generates native OpenCode 2 MCP config and migrates flat V1 entries."""
        servers = self.load_resolved_servers("opencode")
        cfg_file = self.opencode_config_path()
        existing: dict[str, Any] = {}
        raw_existing = ""
        if cfg_file.is_file():
            raw_existing = cfg_file.read_text(encoding="utf-8")
            try:
                existing = parse_jsonc(raw_existing) if cfg_file.suffix == ".jsonc" else json.loads(raw_existing)
            except Exception as exc:
                raise ValueError(f"Could not parse {cfg_file}: invalid JSON/JSONC ({exc})")

        raw_mcp = existing.get("mcp", {})
        if not isinstance(raw_mcp, dict):
            raise ValueError(f"Could not render {cfg_file}: mcp must be an object")
        if "servers" in raw_mcp:
            if not isinstance(raw_mcp["servers"], dict):
                raise ValueError(f"Could not render {cfg_file}: mcp.servers must be an object")
            mcp_config = dict(raw_mcp)
            mcp_servers = dict(raw_mcp["servers"])
        else:
            # The old flat layout is no longer the written contract. Move
            # unknown live servers as well, normalizing fields V2 rejects.
            mcp_config = {}
            mcp_servers = {}
            for name, entry in raw_mcp.items():
                if not isinstance(entry, dict):
                    raise ValueError(f"Could not migrate {cfg_file}: MCP server {name!r} must be an object")
                migrated = dict(entry)
                enabled = migrated.pop("enabled", None)
                if enabled is False:
                    migrated["disabled"] = True
                if isinstance(migrated.get("timeout"), int):
                    migrated["timeout"] = {"execution": migrated["timeout"]}
                oauth = migrated.get("oauth")
                if isinstance(oauth, dict):
                    oauth_fields = {
                        "clientId": "client_id",
                        "clientSecret": "client_secret",
                        "callbackPort": "callback_port",
                        "redirectUri": "redirect_uri",
                        "authServerMetadataUrl": "auth_server_metadata_url",
                    }
                    migrated["oauth"] = {oauth_fields.get(k, k): v for k, v in oauth.items()}
                mcp_servers[name] = migrated
        for retired in self.retired_server_names():
            mcp_servers.pop(retired, None)
        self._drop_unmounted(mcp_servers, servers, cli_target="opencode")
        tools_cfg: dict[str, Any] = dict(existing.get("tools") or {})
        old_tools_cfg = dict(tools_cfg)
        permissions = existing.get("permissions", [])
        if not isinstance(permissions, list):
            raise ValueError(f"Could not render {cfg_file}: permissions must be an array")
        permissions = list(permissions)
        old_permissions = list(permissions)
        for name, srv in servers.items():
            mapped = self._opencode_timeouts(srv.get("timeouts"))
            if srv.get("transport") == "http" or srv.get("url"):
                url_env = srv.get("url_env")
                url = f"{{env:{url_env}}}" if url_env else srv["url"]
                auth_env = srv.get("auth", {}).get("env") if isinstance(srv.get("auth"), dict) else ""
                headers = {"Authorization": f"Bearer {{env:{auth_env}}}"} if auth_env else {}
                entry: dict[str, Any] = {
                    "type": "remote",
                    "url": url,
                }
                if auth_env:
                    entry["headers"] = headers
                    entry["oauth"] = False
                elif srv.get("oauth"):
                    # OpenCode handles the OAuth flow itself (discovery,
                    # dynamic registration, tokens outside the config):
                    # no headers, no client secrets in the config. An
                    # explicit `oauth_client_id` (a public identifier, never
                    # a secret) is forwarded for providers that do not
                    # support dynamic client registration, e.g. Google
                    # Workspace MCP.
                    oauth_config: dict[str, Any] = {}
                    client_id = srv.get("oauth_client_id")
                    if isinstance(client_id, str) and client_id.strip():
                        oauth_config["client_id"] = client_id.strip()
                    if oauth_config:
                        entry["oauth"] = oauth_config
                elif srv.get("oauth") is False and "oauth" in srv:
                    entry["oauth"] = False
            else:
                cmd_list = [srv.get("command", "")] + list(srv.get("args", []))
                entry = {
                    "type": "local",
                    "command": cmd_list,
                }
                if srv.get("env"):
                    entry["environment"] = srv["env"]
            if mapped:
                entry["timeout"] = mapped
            mcp_servers[name] = entry
            # V2 denies tools through permissions, including Code Mode tools.
            deny = srv.get("tools_deny")
            if isinstance(deny, list) and deny:
                for tool in deny:
                    action = re.sub(r"[^A-Za-z0-9_-]", "_", f"{name}_{tool}")
                    rule = {"action": action, "resource": "*", "effect": "deny"}
                    if rule not in permissions:
                        permissions.append(rule)
                    if tools_cfg.get(action) is False:
                        tools_cfg.pop(action)

        # This renderer owns MCP entries, not the user's native tool choices.
        # In particular, websearch remains available as a fallback when the
        # Firecrawl connector is unavailable. One exception, once: the
        # previous renderer forced `tools.websearch = false` together with
        # `websearch = "parallel"` on every machine it touched, so a bare
        # `false` paired with that exact marker is the old engine's
        # fingerprint, not a human choice. It is dropped so the documented
        # fallback lane works again; any other value (true, an object, a
        # named provider) is the user's and stays exactly as it is.
        if tools_cfg.get("websearch") is False and existing.get("websearch") == "parallel":
            tools_cfg.pop("websearch")
            existing.pop("websearch", None)
            drop_engine_websearch = True
        else:
            drop_engine_websearch = False
        if "tools" in existing or tools_cfg:
            existing["tools"] = tools_cfg
        if permissions:
            existing["permissions"] = permissions
        mcp_config["servers"] = mcp_servers
        existing["mcp"] = mcp_config
        if write:
            # JSONC-aware: preserves the existing file's comments instead of
            # overwriting it with plain JSON (which OpenCode wouldn't read).
            if cfg_file.suffix == ".jsonc" and raw_existing.strip():
                content = set_jsonc_top_level_value(raw_existing, "mcp", mcp_config)
                if "tools" in existing and tools_cfg != old_tools_cfg:
                    content = set_jsonc_top_level_value(content, "tools", tools_cfg)
                if permissions and permissions != old_permissions:
                    content = set_jsonc_top_level_value(content, "permissions", permissions)
                if drop_engine_websearch:
                    content = remove_jsonc_top_level_value(content, "websearch")
            else:
                # File missing or empty: no comments to preserve.
                content = json.dumps(existing, indent=2) + "\n"
            self._backup_and_write(cfg_file, content)
        return True, t("OpenCode configuration updated")

    def render_codex(self, write: bool = False) -> tuple[bool, str]:
        """Generates Codex's native MCP configuration (~/.codex/config.toml).

        Additive like the other three CLIs: an existing server that is not in
        the manifest is preserved verbatim, and one that is env-gated but not
        resolvable right now (e.g. the recurring guard running without the
        user's shell environment) keeps the entry already on disk instead of
        deleting it. Only `retired_servers` remove entries; manifest servers
        are always re-emitted fresh.
        """
        servers = self.load_resolved_servers("codex")
        cfg_file = self.home / ".codex" / "config.toml"

        retired = self.retired_server_names()
        _manifest_data = load_mcp_manifest(self.manifest_path) if self.manifest_path.is_file() else {}
        unmounted = {
            name.replace("-", "_")
            for name, srv in _manifest_data.get("servers", {}).items()
            if name not in servers and name not in retired
            and not (
                srv.get("require_env")
                and (str(srv.get("tier", "")).strip().lower() == "core" or srv.get("enabled", False))
                and not (srv.get("lazy") and "codex" in (srv.get("lazy_targets") or ["claude", "codex", "antigravity", "opencode"]))
            )
        }
        managed = {name.replace("-", "_") for name in servers} | {name.replace("-", "_") for name in retired}

        existing_lines: list[str] = []
        preserved_lines: list[str] = []
        if cfg_file.is_file():
            try:
                raw = cfg_file.read_text(encoding="utf-8")
                # Preserves existing non-MCP sections (e.g. [model], general
                # settings) and the mcp_servers entries this engine doesn't own.
                in_mcp_section = False
                keep_current = False
                for line in raw.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("[mcp_servers."):
                        in_mcp_section = True
                        key = stripped[13:-1] if stripped.endswith("]") else stripped[13:]
                        section = key.split(".", 1)[0]
                        keep_current = section not in managed and section not in unmounted
                        if keep_current:
                            preserved_lines.append(line)
                        continue
                    elif stripped.startswith("[") and not stripped.startswith("[mcp_servers."):
                        in_mcp_section = False
                        keep_current = False
                    if not in_mcp_section and not line.startswith("# NeXgen Engine"):
                        existing_lines.append(line)
                    elif in_mcp_section and keep_current:
                        preserved_lines.append(line)
            except OSError:
                existing_lines = []
                preserved_lines = []

        header = "# NeXgen Engine - Codex MCP configuration, auto-generated"
        lines: list[str] = []
        if existing_lines:
            cleaned_existing = "\n".join(existing_lines).strip()
            if cleaned_existing:
                lines.append(cleaned_existing)
                lines.append("")

        lines.append(header)
        lines.append("")
        if preserved_lines:
            lines.append("\n".join(preserved_lines).strip())
            lines.append("")
        for name, srv in servers.items():
            safe_name = name.replace("-", "_")
            lines.append(f"[mcp_servers.{safe_name}]")
            if srv.get("transport") == "http" or srv.get("url"):
                lines.append(f'url = {json.dumps(srv["url"])}')
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
                lines.append(f'command = {json.dumps(srv.get("command", ""))}')
                args_json = json.dumps(srv.get("args", []))
                lines.append(f"args = {args_json}")
                env = srv.get("env", {})
                if env:
                    lines.append(f"[mcp_servers.{safe_name}.env]")
                    for k, v in env.items():
                        lines.append(f'{k} = {json.dumps(str(v))}')
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
        """Makes a .bak-<timestamp> backup and writes the new content atomically.

        A config that already matches is left completely untouched: the guard
        cycle runs twice an hour, and rewriting a byte-identical file every
        cycle changes mtimes and piles up backups for nothing.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file():
            try:
                if path.read_text(encoding="utf-8") == content:
                    return
            except (OSError, UnicodeDecodeError):
                pass
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
