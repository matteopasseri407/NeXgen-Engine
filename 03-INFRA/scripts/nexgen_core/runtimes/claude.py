"""Adattatore Claude Code: postura in ~/.claude/settings.json + hook PreToolUse.

Claude e' l'unica CLI di questo pacchetto che parla gia' nativamente lo
stesso JSON che il corpo del guardrail si aspetta in stdin/stdout: nessun
adattatore intermedio serve, il corpo va registrato direttamente come
comando dell'hook.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from nexgen_core.runtimes.base import GuardrailError, Runtime

#: Vocabolario neutro -> valore che Claude capisce (permissions.defaultMode).
#: Verificato sul campo, non inventato: la spellatura esatta del binario.
_POSTURE_TO_CLAUDE = {"bypass": "bypassPermissions", "accept-edits": "acceptEdits", "ask": "default"}
_CLAUDE_TO_POSTURE = {v: k for k, v in _POSTURE_TO_CLAUDE.items()}


class ClaudeRuntime(Runtime):
    name = "claude"

    def _settings_path(self, home: Path) -> Path:
        return home / ".claude" / "settings.json"

    def is_installed(self, home: Path) -> bool:
        if shutil.which("claude"):
            return True
        # settings.json e' scritto solo da Claude Code stesso al primo
        # avvio -- a differenza di ~/.claude.json (config MCP), che questo
        # layer riscrive ad ogni ciclo indipendentemente dall'installazione.
        return self._settings_path(home).is_file()

    def _load_settings(self, home: Path) -> dict[str, Any] | None:
        """None se il file non esiste (CLI mai avviata qui: nulla su cui
        fare merge). Solleva GuardrailError se esiste ma e' inaffidabile da
        modificare -- non JSON valido, radice non un oggetto, o una forma di
        `hooks` che una scrittura a meta' romperebbe."""
        path = self._settings_path(home)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise GuardrailError(f"claude: {path} non e' JSON valido ({exc})") from exc
        if not isinstance(data, dict):
            raise GuardrailError(f"claude: la radice di {path} non e' un oggetto")
        hooks = data.get("hooks")
        if hooks is not None:
            if not isinstance(hooks, dict):
                raise GuardrailError(f"claude: {path}: hooks deve essere un oggetto")
            for event, matchers in hooks.items():
                if not isinstance(matchers, list):
                    raise GuardrailError(f"claude: {path}: hooks.{event} deve essere una lista")
                if any(not isinstance(m, dict) for m in matchers):
                    raise GuardrailError(f"claude: {path}: hooks.{event} contiene una voce non-oggetto")
        return data

    def read_posture(self, home: Path) -> str | None:
        try:
            data = self._load_settings(home)
        except GuardrailError:
            return None
        if not data:
            return None
        mode = (data.get("permissions") or {}).get("defaultMode")
        return _CLAUDE_TO_POSTURE.get(mode)

    def apply_posture(self, home: Path, posture: str) -> str | None:
        if posture not in _POSTURE_TO_CLAUDE:
            return None
        data = self._load_settings(home)
        if data is None:
            return None  # Claude mai avviata qui: nessuna postura da applicare
        before = json.dumps(data, sort_keys=True)
        perms = data.setdefault("permissions", {})
        if not isinstance(perms, dict):
            raise GuardrailError("claude: settings.permissions non e' un oggetto")
        perms["defaultMode"] = _POSTURE_TO_CLAUDE[posture]
        if posture == "bypass":
            # Senza questo Claude blocca su un dialogo di conferma
            # interattivo all'avvio, a cui una guardia in background non
            # puo' rispondere.
            data["skipDangerousModePermissionPrompt"] = True
        if json.dumps(data, sort_keys=True) == before:
            return None
        path = self._settings_path(home)
        self.backup(path)
        self.atomic_write(path, json.dumps(data, indent=2) + "\n")
        return f"claude: postura '{posture}' applicata in {path}"

    def install_guardrail(self, home: Path, hook_source: Path, engine_hooks_dir: Path) -> str | None:
        del engine_hooks_dir  # Claude parla gia' il dialetto nativo del corpo
        data = self._load_settings(home)
        if data is None:
            return None
        claude_dir = home / ".claude"
        dst = claude_dir / hook_source.name
        deployed = self.deploy_bytes(dst, hook_source.read_bytes())

        command = f'node "{dst}"'
        hooks = data.setdefault("hooks", {})
        if not isinstance(hooks, dict):
            raise GuardrailError("claude: settings.hooks non e' un oggetto")
        entries = hooks.setdefault("PreToolUse", [])
        if not isinstance(entries, list):
            raise GuardrailError("claude: settings.hooks.PreToolUse non e' una lista")
        already_registered = any(
            h.get("command") == command
            for matcher in entries if isinstance(matcher, dict)
            for h in matcher.get("hooks", [])
        )
        if already_registered:
            return f"claude: corpo del guardrail aggiornato in {dst}" if deployed else None

        entries.append({
            "matcher": "Bash",
            "hooks": [{"type": "command", "command": command, "timeout": 5}],
        })
        path = self._settings_path(home)
        self.backup(path)
        self.atomic_write(path, json.dumps(data, indent=2) + "\n")
        return f"claude: hook guardrail registrato in {path}"
