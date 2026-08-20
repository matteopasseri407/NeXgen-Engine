"""Generatore dei launcher, da una sola descrizione per entrambe le piattaforme.

C'è un comando vero, `nexgen`. Tutti gli altri nomi sono alias ereditati: li
generiamo lo stesso perché una macchina già installata li invoca per percorso,
e perché l'aggiornamento alla versione nuova viene eseguito dal `nexgen-update`
di quella vecchia. Rompere l'alberatura dei nomi significa lasciare tre
macchine con i comandi a metà.

Un alias non è una copia della logica: è `nexgen` con dei verbi già davanti.
"""
from __future__ import annotations

import stat
import sys
from pathlib import Path

#: Il comando vero.
PRIMARY = "nexgen"

#: I nomi ereditati, con i verbi che ciascuno antepone. La lista vuota
#: significa "passa gli argomenti così come sono": `agent-sync doctor -v`
#: diventa `nexgen doctor -v` senza traduzioni.
LEGACY_ALIASES: dict[str, list[str]] = {
    "agent-sync": [],
    "agent-doctor": ["doctor"],
    "vault-push": ["vault", "push"],
    "vault-groom": ["vault", "groom"],
    "vault-map": ["vault", "map"],
    "agent-now": ["tool", "now"],
    "agent-open-folder": ["tool", "open"],
    "agent-chrome": ["tool", "chrome"],
    "firecrawl-local": ["tool", "firecrawl"],
    "nexgen-update": ["update"],
    "skills-sync": ["skill"],
    "agent-skill": ["skill"],
    "council": ["council"],
}

#: Compatibilità all'indietro per chi importa la tabella storica.
COMMANDS: list[tuple[str, str]] = [(PRIMARY, "nexgen_core/cli/__init__.py")] + [
    (name, "nexgen_core/cli/__init__.py") for name in LEGACY_ALIASES
]

_POSIX_TEMPLATE = """#!/usr/bin/env sh
# NeXgen Engine — generato automaticamente, non modificare a mano.
# {note}
NEXGEN_ENTRY="{entry}"
if [ -n "${{AGENT_ENGINE_ROOT:-}}" ] && [ -f "$AGENT_ENGINE_ROOT/scripts/nexgen_core/cli/__init__.py" ]; then
    NEXGEN_ENTRY="$AGENT_ENGINE_ROOT/scripts/nexgen_core/cli/__init__.py"
fi
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        exec "$candidate" "$NEXGEN_ENTRY" {prefix}"$@"
    fi
done
echo "NeXgen: Python 3 non è nel PATH di questo sistema." >&2
exit 1
"""

_WINDOWS_TEMPLATE = """@echo off
rem NeXgen Engine — generato automaticamente, non modificare a mano.
rem {note}
setlocal
set "NEXGEN_ENTRY={entry}"
if defined AGENT_ENGINE_ROOT (
    if exist "%AGENT_ENGINE_ROOT%\\scripts\\nexgen_core\\cli\\__init__.py" set "NEXGEN_ENTRY=%AGENT_ENGINE_ROOT%\\scripts\\nexgen_core\\cli\\__init__.py"
)
where py >nul 2>&1
if %ERRORLEVEL% equ 0 (
    py -3 "%NEXGEN_ENTRY%" {prefix}%*
    exit /b %ERRORLEVEL%
)
where python >nul 2>&1
if %ERRORLEVEL% equ 0 (
    python "%NEXGEN_ENTRY%" {prefix}%*
    exit /b %ERRORLEVEL%
)
echo NeXgen: Python 3 non e' nel PATH di questo sistema. >&2
exit /b 1
"""


def ensure_executable(path: Path) -> None:
    """Imposta il bit di esecuzione su sistemi POSIX."""
    if sys.platform != "win32" and path.exists():
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _render(name: str, prefix: list[str], entry: Path, windows: bool) -> str:
    note = (
        f"'{name}' è il nome storico di '{PRIMARY} {' '.join(prefix)}'."
        if prefix
        else f"'{name}' è il nome storico di '{PRIMARY}'."
    )
    prefix_str = ("".join(f'"{v}" ' for v in prefix)) if prefix else ""
    template = _WINDOWS_TEMPLATE if windows else _POSIX_TEMPLATE
    return template.format(note=note, entry=str(entry), prefix=prefix_str)


def install_shims(
    scripts_dir: Path | None = None,
    bin_dir: Path | None = None,
    home: Path | None = None,
) -> list[str]:
    """Genera i launcher per la piattaforma corrente e restituisce cosa ha scritto.

    È idempotente: rigenerare non cambia niente se il contenuto combacia già,
    ed è per questo che il ciclo di guardia può chiamarla a ogni giro per
    riparare in silenzio un comando cancellato o rotto.
    """
    home_dir = home or Path.home()
    target_bin = bin_dir or (home_dir / ".local" / "bin")
    target_bin.mkdir(parents=True, exist_ok=True)

    base_scripts = scripts_dir or Path(__file__).resolve().parents[1]
    entry = base_scripts / "nexgen_core" / "cli" / "__init__.py"
    windows = sys.platform == "win32"
    suffix = ".cmd" if windows else ""

    installed: list[str] = []
    for name, prefix in [(PRIMARY, [])] + sorted(LEGACY_ALIASES.items()):
        launcher = target_bin / f"{name}{suffix}"
        content = _render(name, prefix, entry, windows)
        if not (launcher.is_file() and launcher.read_text(encoding="utf-8") == content):
            if launcher.is_symlink():
                launcher.unlink()
            launcher.write_text(content, encoding="utf-8")
        ensure_executable(launcher)
        installed.append(str(launcher))

    return installed
