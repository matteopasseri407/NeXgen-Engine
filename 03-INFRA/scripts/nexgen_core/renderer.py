#!/usr/bin/env python3
"""Generatore e render delle configurazioni MCP per le 4 CLI (Claude, Codex, OpenCode, Antigravity).

Ordini e regole del contratto:
1. Manifest canonico (mcp/manifest.yaml) come single source of truth.
2. Risoluzione comandi stdio (command + args espansi) e server HTTP con token env-ref.
3. Supporto nativo per le 4 CLI: Claude, Codex (TOML), OpenCode, Antigravity (bridge).
4. Preservazione additiva dei server live non presenti nel manifest, tranne
   quelli elencati in `retired_servers`: quello è il meccanismo di rimozione
   esplicito e cross-CLI, e vince sempre sulla preservazione additiva.
5. Scrittura atomica con backup di sicurezza e gestione errori rigorosa.
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
from nexgen_core.jsonc import parse_jsonc, set_jsonc_top_level_value
from nexgen_core.paths import resolve_engine_root, resolve_vault_data

IS_WINDOWS = platform.system() == "Windows"
MCP_REMOTE_PACKAGE = "mcp-remote@0.1.38"


class McpRenderer:
    """Render delle configurazioni MCP per ciascuna CLI supportata."""

    def __init__(
        self,
        vault_data: Path | None = None,
        engine_root: Path | None = None,
        home: Path | None = None,
    ) -> None:
        self.home = home or Path.home()
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
        """Normalizza eseguibili comuni su Windows (npx -> npx.cmd, python3 -> python)."""
        lower = exe.lower()
        if lower == "npx":
            return "npx.cmd"
        if lower == "python3":
            return "python"
        return exe

    def _opencode_config_path(self) -> Path:
        """Restituisce il percorso nativo della config OpenCode.

        OpenCode usa ``opencode.jsonc`` (commenti/virgole finali). La release
        risolveva il file ESISTENTE con priorità jsonc > json > config.json,
        così una macchina già configurata veniva aggiornata sul file che
        OpenCode legge davvero, senza crearne un secondo accanto.
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
        """I nomi dei connettori ritirati: il meccanismo di rimozione esplicito.

        `load_mcp_manifest` esclude già questi nomi dai server attivi, ma le
        tre CLI che preservano additivamente i server live esistenti (Claude,
        Antigravity, OpenCode) non li toglierebbero mai da sole: un connettore
        ritirato deve sparire da ogni configurazione resa, non solo mancare
        nelle nuove. Rimuoverlo non è un guasto e non va riportato come tale.
        """
        if not self.manifest_path.is_file():
            return set()
        data = load_mcp_manifest(self.manifest_path)
        return set(data.get("retired_servers", set()))

    def load_resolved_servers(self, cli_target: str) -> dict[str, dict[str, Any]]:
        """Carica i server MCP risolti e filtrati per una specifica CLI."""
        if not self.manifest_path.is_file():
            return {}

        data = load_mcp_manifest(self.manifest_path)
        raw_servers = data.get("servers", {})
        resolved: dict[str, dict[str, Any]] = {}

        for name, srv in raw_servers.items():
            targets = srv.get("targets", ["claude", "codex", "antigravity", "opencode"])
            if cli_target not in targets:
                continue

            # Controllo require_env
            req_env = srv.get("require_env")
            if req_env:
                env_val = os.environ.get(req_env)
                if not env_val:
                    continue

            # Override per Windows
            entry = dict(srv)
            if IS_WINDOWS and "windows" in entry:
                win_override = entry.pop("windows")
                if isinstance(win_override, dict):
                    entry.update(win_override)

            # Risoluzione command ed args
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

            # Risoluzione URL
            if entry.get("url"):
                entry["url"] = expand_placeholders(str(entry["url"]), self.path_placeholders)

            # Risoluzione env
            if "env" in entry and isinstance(entry["env"], dict):
                entry["env"] = {
                    k: expand_placeholders(str(v), self.path_placeholders)
                    for k, v in entry["env"].items()
                }

            resolved[name] = entry

        return resolved

    def render_claude(self, write: bool = False) -> tuple[bool, str]:
        """Genera la configurazione MCP per Claude Code (~/.claude.json)."""
        servers = self.load_resolved_servers("claude")
        cfg_file = self.home / ".claude.json"
        existing: dict[str, Any] = {}
        if cfg_file.is_file():
            try:
                existing = json.loads(cfg_file.read_text(encoding="utf-8"))
            except Exception as exc:
                raise ValueError(f"Impossibile analizzare {cfg_file}: JSON non valido ({exc})")

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
        return True, "Configurazione Claude aggiornata"

    #: Antigravity legge la stessa configurazione da tre percorsi diversi, a
    #: seconda di come lo si avvia. Il file vero è uno solo e gli altri tre lo
    #: raggiungono: scriverne uno e sperare che sia quello giusto significa
    #: lasciare due varianti su tre con una configurazione vecchia.
    ANTIGRAVITY_CONSUMER_DIRS = ("antigravity-cli", "antigravity-ide", "config")

    def render_antigravity(self, write: bool = False) -> tuple[bool, str]:
        """Genera la configurazione MCP di Antigravity e la fa raggiungere ai suoi consumatori."""
        servers = self.load_resolved_servers("antigravity")
        cfg_file = self.home / ".gemini" / "antigravity" / "mcp_config.json"
        existing: dict[str, Any] = {}
        if cfg_file.is_file():
            try:
                existing = json.loads(cfg_file.read_text(encoding="utf-8"))
            except Exception as exc:
                raise ValueError(f"Impossibile analizzare {cfg_file}: JSON non valido ({exc})")

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
        return True, "Configurazione Antigravity aggiornata"

    def _fan_out_antigravity(self, canonical: Path) -> None:
        """Fa puntare al file canonico tutti i percorsi da cui Antigravity legge.

        Dove i collegamenti non si possono creare (Windows senza i privilegi)
        si copia: l'importante è che nessuna variante resti indietro.
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
        """Genera la configurazione MCP per OpenCode (opencode.jsonc, JSONC-aware)."""
        servers = self.load_resolved_servers("opencode")
        cfg_file = self._opencode_config_path()
        existing: dict[str, Any] = {}
        raw_existing = ""
        if cfg_file.is_file():
            raw_existing = cfg_file.read_text(encoding="utf-8")
            try:
                existing = parse_jsonc(raw_existing) if cfg_file.suffix == ".jsonc" else json.loads(raw_existing)
            except Exception as exc:
                raise ValueError(f"Impossibile analizzare {cfg_file}: JSON/JSONC non valido ({exc})")

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
            # JSONC-aware: preserva i commenti del file esistente invece di
            # sovrascriverlo con JSON puro (che OpenCode non leggerebbe).
            if cfg_file.suffix == ".jsonc" and raw_existing.strip():
                content = set_jsonc_top_level_value(raw_existing, "mcp", mcp_servers)
            else:
                # File assente o vuoto: non ci sono commenti da preservare.
                content = json.dumps(existing, indent=2) + "\n"
            self._backup_and_write(cfg_file, content)
        return True, "Configurazione OpenCode aggiornata"

    def render_codex(self, write: bool = False) -> tuple[bool, str]:
        """Genera la configurazione MCP nativa per Codex (~/.codex/config.toml)."""
        servers = self.load_resolved_servers("codex")
        cfg_file = self.home / ".codex" / "config.toml"

        existing_lines: list[str] = []
        if cfg_file.is_file():
            try:
                raw = cfg_file.read_text(encoding="utf-8")
                # Preserva sezioni non-MCP esistenti (es. [model], impostazioni generali)
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

        header = "# NeXgen Engine - Configurazione MCP Codex generata automaticamente"
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
        return True, "Configurazione Codex aggiornata"

    def render_all(self, write: bool = False) -> dict[str, bool]:
        """Esegue il render per tutte le 4 CLI."""
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
        """Esegue backup .bak-<timestamp> e scrive il nuovo contenuto in modo atomico."""
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file():
            stamp = time.strftime("%Y%m%d-%H%M%S")
            bak = path.with_name(f"{path.name}.bak-{stamp}")
            shutil.copy2(path, bak)
            # Mantieni solo gli ultimi 3 backup
            backs = sorted(path.parent.glob(f"{path.name}.bak-*"))
            for old in backs[:-3]:
                old.unlink(missing_ok=True)

        # Scrittura atomica tramite tempfile nello stesso filesystem
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
