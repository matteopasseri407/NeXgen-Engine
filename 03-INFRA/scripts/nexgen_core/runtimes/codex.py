"""Adattatore Codex: sola postura 'bypass' in ~/.codex/config.toml, nessun guardrail.

Solo 'bypass' ha un rendering verificato (contro il flag one-shot
--dangerously-bypass-approvals-and-sandbox del binario installato):
accept-edits/ask non hanno un default confermato e restano fuori apposta.

Nessun guardrail per Codex, ne' qui ne' mai senza una decisione esplicita di
chi possiede la macchina: i suoi hook stanno dietro un consenso per-hash che
solo una persona puo' dare in sessione interattiva (o un flag
--dangerously-bypass-hook-trust che nessun provisioner deve premere da solo).
Un hook scritto qui non girerebbe finche' quella decisione non viene presa
altrove -- una lacuna silenziosa con l'aspetto di un guardrail funzionante,
peggio di non averne affatto. agent-doctor lo segnala ad ogni esecuzione:
Codex e' l'unica CLI che gira senza rete, di proposito.
"""
from __future__ import annotations

import re
import shutil
import tomllib
from pathlib import Path

from nexgen_core.runtimes.base import GuardrailError, Runtime

_POSTURE_RENDER = {"bypass": {"approval_policy": "never", "sandbox_mode": "danger-full-access"}}


def _root_table_end(lines: list[str]) -> int:
    """Indice della prima riga `[tabella]`: tutto cio' che viene prima
    (righe vuote, commenti, chiavi bare) e' territorio della root table."""
    for i, line in enumerate(lines):
        if re.match(r"^[ \t]*\[", line):
            return i
    return len(lines)


def _set_root_string(text: str, key: str, value: str) -> str:
    """Imposta chirurgicamente UNA chiave stringa della root table: tocca
    solo quella riga (o ne inserisce una prima della prima [tabella]),
    lasciando ogni commento e sezione intatti -- mai una riscrittura totale
    del config.toml dell'utente. Il risultato viene ri-parsato e verificato
    prima di essere restituito."""
    lines = text.split("\n")
    root_end = _root_table_end(lines)
    pattern = re.compile(rf"^[ \t]*{re.escape(key)}[ \t]*=.*$")
    rendered = f'{key} = "{value}"'
    for i in range(root_end):
        if pattern.match(lines[i]):
            lines[i] = rendered
            break
    else:
        lines.insert(root_end, rendered)
    result = "\n".join(lines)
    reparsed = tomllib.loads(result)
    if reparsed.get(key) != value:
        raise GuardrailError(f"codex: impossibile impostare la chiave TOML {key!r}")
    return result


class CodexRuntime(Runtime):
    name = "codex"

    def _config_path(self, home: Path) -> Path:
        return home / ".codex" / "config.toml"

    def is_installed(self, home: Path) -> bool:
        del home
        # config.toml NON e' un segnale valido: il renderer MCP di questo
        # stesso layer lo crea da zero ad ogni ciclo anche su una macchina
        # dove Codex non e' mai stato installato. Solo il binario sul PATH
        # e' un'impronta che appartiene esclusivamente al prodotto.
        return bool(shutil.which("codex"))

    def read_posture(self, home: Path) -> str | None:
        path = self._config_path(home)
        if not path.is_file():
            return None
        try:
            current = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError:
            return None
        for posture, desired in _POSTURE_RENDER.items():
            if all(current.get(k) == v for k, v in desired.items()):
                return posture
        return None

    def apply_posture(self, home: Path, posture: str) -> str | None:
        desired = _POSTURE_RENDER.get(posture)
        if desired is None:
            return None
        path = self._config_path(home)
        if not path.is_file():
            return None  # Codex mai avviato qui: nessuna postura da applicare
        raw = path.read_text(encoding="utf-8")
        try:
            current = tomllib.loads(raw)
        except tomllib.TOMLDecodeError as exc:
            raise GuardrailError(f"codex: {path} non e' TOML valido ({exc})") from exc
        if all(current.get(k) == v for k, v in desired.items()):
            return None
        text = raw
        for key, value in desired.items():
            text = _set_root_string(text, key, value)
        self.backup(path)
        self.atomic_write(path, text)
        return f"codex: postura '{posture}' applicata in {path}"

    def install_guardrail(self, home: Path, hook_source: Path, engine_hooks_dir: Path) -> str | None:
        del home, hook_source, engine_hooks_dir
        return None  # Nessun aggancio guardrail verificato per Codex (vedi sopra)
