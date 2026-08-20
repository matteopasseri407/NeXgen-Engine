"""Adattatore Antigravity: postura in settings.json + hook PreToolUse in hooks.json.

Antigravity ha un hook a riga di comando REALE (a differenza della callback
in-process di OpenCode), ma con una propria forma JSON: l'adattatore statico
in agent-universal-layer/hooks/antigravity-guardrail-adapter.mjs traduce
{toolCall, workspacePaths, ...} nello stesso stdin/stdout che il corpo del
guardrail gia' parla per Claude.
"""
from __future__ import annotations

import json
import platform
import shutil
from pathlib import Path
from typing import Any

from nexgen_core.runtimes.base import GuardrailError, Runtime

_IS_WINDOWS = platform.system() == "Windows"

#: Vocabolario neutro -> chiavi Antigravity (verificato contro la
#: documentazione di riferimento imbarcata nel binario installato). Solo
#: 'bypass' e' verificato: non c'e' una separazione pulita accept-edits,
#: perche' toolPermission (shell) non ha un equivalente per i file. Scrivere
#: SOLO toolPermission lascerebbe le modifiche ai file ancora bloccate --
#: un mezzo-bypass che sembra applicato e non lo e'.
_POSTURE_RENDER = {"bypass": {"toolPermission": "always-proceed", "artifactReviewPolicy": "always-proceed"}}

_ADAPTER_NAME = "antigravity-guardrail-adapter.mjs"


class AntigravityRuntime(Runtime):
    name = "antigravity"

    def _settings_path(self, home: Path) -> Path:
        return home / ".gemini" / "antigravity-cli" / "settings.json"

    def _hooks_path(self, home: Path) -> Path:
        return home / ".gemini" / "config" / "hooks.json"

    def is_installed(self, home: Path) -> bool:
        if shutil.which("agy"):
            return True
        names = ("agy.exe", "agy.cmd", "agy") if _IS_WINDOWS else ("agy",)
        if any((home / ".local" / "bin" / name).is_file() for name in names):
            return True
        # settings.json e' scritto solo da Antigravity stesso al primo
        # avvio -- a differenza di mcp_config.json, che questo layer crea da
        # zero ad ogni ciclo indipendentemente dall'installazione.
        return self._settings_path(home).is_file()

    def _load_json(self, path: Path, *, label: str) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise GuardrailError(f"antigravity: {label} non e' JSON valido ({exc})") from exc
        if not isinstance(data, dict):
            raise GuardrailError(f"antigravity: la radice di {label} non e' un oggetto")
        return data

    def read_posture(self, home: Path) -> str | None:
        try:
            data = self._load_json(self._settings_path(home), label="settings.json")
        except GuardrailError:
            return None
        if not data:
            return None
        for posture, desired in _POSTURE_RENDER.items():
            if all(data.get(k) == v for k, v in desired.items()):
                return posture
        return None

    def apply_posture(self, home: Path, posture: str) -> str | None:
        desired = _POSTURE_RENDER.get(posture)
        if desired is None:
            return None
        path = self._settings_path(home)
        data = self._load_json(path, label="settings.json")
        if data is None:
            return None  # Antigravity mai avviata qui: nessuna postura da applicare
        if all(data.get(k) == v for k, v in desired.items()):
            return None
        data.update(desired)
        self.backup(path)
        self.atomic_write(path, json.dumps(data, indent=2) + "\n")
        return f"antigravity: postura '{posture}' applicata in {path}"

    def install_guardrail(self, home: Path, hook_source: Path, engine_hooks_dir: Path) -> str | None:
        if not self._settings_path(home).is_file():
            return None  # Antigravity mai avviata qui: nessun guardrail da installare
        hooks_path = self._hooks_path(home)
        adapter_dir = hooks_path.parent

        body_dst = adapter_dir / "nexgen-guardrail-hooks" / hook_source.name
        body_changed = self.deploy_bytes(body_dst, hook_source.read_bytes())

        adapter_src = engine_hooks_dir / _ADAPTER_NAME
        if not adapter_src.is_file():
            raise GuardrailError(f"antigravity: adattatore engine mancante ({adapter_src})")
        adapter_dst = adapter_dir / _ADAPTER_NAME
        adapter_changed = self.deploy_bytes(adapter_dst, adapter_src.read_bytes())

        sidecar_path = adapter_dir / "nexgen-guardrail.config.json"
        # Il timeout esterno dell'hook di Antigravity uccide l'INTERO
        # comando; l'adattatore applica gia' il proprio timeout per-corpo
        # via subprocess, quindi il buffer qui evita che il kill esterno
        # scatti prima che quello interno abbia la sua occasione.
        timeout = 5
        sidecar_content = json.dumps({"hooks": [{"file": str(body_dst), "timeout": timeout}]}, indent=2) + "\n"
        sidecar_changed = not sidecar_path.is_file() or sidecar_path.read_text(encoding="utf-8") != sidecar_content
        if sidecar_changed:
            self.atomic_write(sidecar_path, sidecar_content)

        desired_entry = {
            "enabled": True,
            "PreToolUse": [{
                "matcher": "run_command",
                "hooks": [{"type": "command", "command": f'node "{adapter_dst}"', "timeout": timeout + 5}],
            }],
        }
        current = self._load_json(hooks_path, label="hooks.json") or {}
        # Solo la propria chiave: qualunque altro hook -- dell'utente o di
        # un altro tool -- in questo file resta esattamente com'era.
        if current.get("nexgen-guardrail") == desired_entry:
            registered = False
        else:
            self.backup(hooks_path)
            updated = {**current, "nexgen-guardrail": desired_entry}
            self.atomic_write(hooks_path, json.dumps(updated, indent=2) + "\n")
            registered = True

        if registered:
            return f"antigravity: guardrail registrato in {hooks_path}"
        if body_changed or adapter_changed or sidecar_changed:
            return f"antigravity: corpo/adattatore guardrail aggiornato in {adapter_dir}"
        return None
