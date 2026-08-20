"""Adattatore OpenCode: postura in opencode.jsonc + plugin guardrail nativo.

OpenCode non parla il JSON di Claude: il suo unico hook con potere di veto e'
`permission.ask`, una callback JS caricata in-process, non un comando
lanciato in un processo separato. L'adattatore statico in
agent-universal-layer/hooks/opencode-guardrail-plugin.mjs (gia' pronto,
mai toccato qui) traduce quella callback nello stesso stdin/stdout JSON che
il corpo del guardrail gia' parla per Claude -- una policy, tre CLI, mai
duplicata.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
from pathlib import Path
from typing import Any

from nexgen_core.jsonc import parse_jsonc, set_jsonc_top_level_value
from nexgen_core.runtimes.base import GuardrailError, Runtime

_IS_WINDOWS = platform.system() == "Windows"

#: Vocabolario neutro -> permission.{edit,bash} (verificato contro le type
#: definition installate di @opencode-ai/sdk). Solo queste due dimensioni:
#: le altre chiavi di permission.* (webfetch, doom_loop, ...) restano
#: qualunque cosa l'utente le abbia impostate.
_POSTURE_RENDER = {
    "bypass": {"edit": "allow", "bash": "allow"},
    "accept-edits": {"edit": "allow", "bash": "ask"},
}

_ADAPTER_NAME = "opencode-guardrail-plugin.mjs"


class OpenCodeRuntime(Runtime):
    name = "opencode"

    def _bin_names(self) -> tuple[str, ...]:
        return ("opencode.exe", "opencode.cmd", "opencode") if _IS_WINDOWS else ("opencode",)

    def is_installed(self, home: Path) -> bool:
        if shutil.which("opencode"):
            return True
        # opencode.jsonc NON e' un segnale valido: il renderer MCP di questo
        # layer lo crea da zero ad ogni ciclo anche se OpenCode non e' mai
        # stato lanciato. La cartella ~/.opencode/bin appartiene solo al
        # programma di installazione ufficiale.
        bin_dir = home / ".opencode" / "bin"
        return any((bin_dir / name).is_file() for name in self._bin_names())

    def _config_path(self, home: Path) -> Path:
        """Il file di config REALE, con la stessa precedenza jsonc > json >
        config.json che OpenCode stesso usa per risolverlo -- una macchina
        gia' configurata va aggiornata sul file che legge davvero, mai su
        una copia nuova accanto."""
        candidates_dirs = [home / ".config" / "opencode"]
        if _IS_WINDOWS:
            appdata = Path(os.environ.get("APPDATA") or (home / "AppData" / "Roaming"))
            candidates_dirs.append(appdata / "opencode")
        for base in candidates_dirs:
            for name in ("opencode.jsonc", "opencode.json", "config.json"):
                candidate = base / name
                if candidate.is_file():
                    return candidate
        return candidates_dirs[0] / "opencode.jsonc"

    def _load(self, path: Path) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        raw = path.read_text(encoding="utf-8")
        try:
            data = parse_jsonc(raw) if path.suffix == ".jsonc" else json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            raise GuardrailError(f"opencode: {path.name} non e' JSON/JSONC valido ({exc})") from exc
        if not isinstance(data, dict):
            raise GuardrailError(f"opencode: la radice di {path.name} non e' un oggetto")
        return data

    def read_posture(self, home: Path) -> str | None:
        try:
            data = self._load(self._config_path(home))
        except GuardrailError:
            return None
        if not data:
            return None
        permission = data.get("permission")
        if not isinstance(permission, dict):
            return None
        for posture, desired in _POSTURE_RENDER.items():
            if all(permission.get(k) == v for k, v in desired.items()):
                return posture
        return None

    def apply_posture(self, home: Path, posture: str) -> str | None:
        desired = _POSTURE_RENDER.get(posture)
        if desired is None:
            return None
        path = self._config_path(home)
        data = self._load(path)
        if data is None:
            return None  # OpenCode mai avviato qui: nessuna postura da applicare
        current_permission = data.get("permission")
        if current_permission is not None and not isinstance(current_permission, dict):
            raise GuardrailError(f"opencode: {path.name}: 'permission' non e' un oggetto")
        permission = dict(current_permission or {})
        if all(permission.get(k) == v for k, v in desired.items()):
            return None
        permission.update(desired)
        self._write_key(path, "permission", permission)
        return f"opencode: postura '{posture}' applicata in {path}"

    def _write_key(self, path: Path, key: str, value: Any) -> None:
        raw = path.read_text(encoding="utf-8") if path.is_file() else "{}\n"
        self.backup(path)
        if path.suffix == ".jsonc":
            updated = set_jsonc_top_level_value(raw, key, value)
        else:
            data = json.loads(raw) if raw.strip() else {}
            data[key] = value
            updated = json.dumps(data, indent=2) + "\n"
        self.atomic_write(path, updated)

    def install_guardrail(self, home: Path, hook_source: Path, engine_hooks_dir: Path) -> str | None:
        config_path = self._config_path(home)
        if not config_path.is_file():
            return None  # OpenCode mai avviato qui: nessun guardrail da installare
        plugin_dir = config_path.parent

        # 1) Corpo del guardrail (la policy, privata del Vault).
        body_dst = plugin_dir / "nexgen-guardrail-hooks" / hook_source.name
        body_changed = self.deploy_bytes(body_dst, hook_source.read_bytes())

        # 2) Adattatore statico dell'engine (traduce permission.ask -> stdin/stdout).
        adapter_src = engine_hooks_dir / _ADAPTER_NAME
        if not adapter_src.is_file():
            raise GuardrailError(f"opencode: adattatore engine mancante ({adapter_src})")
        adapter_dst = plugin_dir / _ADAPTER_NAME
        adapter_changed = self.deploy_bytes(adapter_dst, adapter_src.read_bytes())

        # 3) Sidecar: quale corpo eseguire e con quale timeout, letto fresco
        #    dall'adattatore ad ogni chiamata (nessun riavvio di OpenCode
        #    serve per far vedere un manifest cambiato).
        sidecar_path = plugin_dir / "nexgen-guardrail.config.json"
        sidecar_content = json.dumps({"hooks": [{"file": str(body_dst), "timeout": 5}]}, indent=2) + "\n"
        sidecar_changed = not sidecar_path.is_file() or sidecar_path.read_text(encoding="utf-8") != sidecar_content
        if sidecar_changed:
            self.atomic_write(sidecar_path, sidecar_content)

        # 4) Registrazione nel proprio array "plugin" -- append e dedup,
        #    ogni altro plugin dell'utente resta esattamente com'era.
        plugin_registered = self._register_plugin(config_path, adapter_dst)

        if plugin_registered:
            return f"opencode: guardrail registrato in {config_path}"
        if body_changed or adapter_changed or sidecar_changed:
            return f"opencode: corpo/adattatore guardrail aggiornato in {plugin_dir}"
        return None

    def _register_plugin(self, config_path: Path, adapter_dst: Path) -> bool:
        config = self._load(config_path)
        if config is None:
            return False
        plugins = config.get("plugin", [])
        if not isinstance(plugins, list):
            raise GuardrailError(f"opencode: {config_path.name}: 'plugin' non e' una lista")
        try:
            entry = adapter_dst.resolve().as_uri()
        except (OSError, ValueError) as exc:
            raise GuardrailError(f"opencode: impossibile risolvere {adapter_dst} ({exc})") from exc
        if any(isinstance(p, str) and p.strip() == entry for p in plugins):
            return False
        self._write_key(config_path, "plugin", [*plugins, entry])
        return True
