"""Generatore unificato dei launcher/shim per Linux e Windows.

Principio: Una sola sorgente, i derivati si generano.
- Su Linux: crea symlink diretti in ~/.local/bin/ verso i comandi Python con shebang e bit +x.
- Su Windows: genera i file .cmd da un unico template condiviso in %USERPROFILE%\\.local\\bin\\.
"""
from __future__ import annotations

import stat
import sys
from pathlib import Path

# Comandi principali del layer agentico
COMMANDS = [
    ("agent-sync", "nexgen_core/cli.py"),
    ("agent-doctor", "nexgen_core/doctor.py"),
    ("vault-push", "nexgen_core/publisher.py"),
    ("agent-now", "nexgen_core/tools/now.py"),
    ("agent-open-folder", "nexgen_core/tools/open_folder.py"),
    ("agent-chrome", "nexgen_core/tools/chrome.py"),
    ("firecrawl-local", "nexgen_core/tools/firecrawl.py"),
    ("nexgen-update", "nexgen_core/updater.py"),
    ("skills-sync", "nexgen_core/skills.py"),
    ("agent-skill", "nexgen_core/skills.py"),
    ("council", "nexgen_core/tools/council.py"),
    ("vault-groom", "nexgen_core/tools/vault_groom.py"),
    ("vault-map", "nexgen_core/tools/vault_map.py"),
]

WINDOWS_CMD_TEMPLATE = """@echo off
rem NeXgen Engine v2 — generato automaticamente dall'installer/sync.
setlocal
set "TARGET_PY={abs_path}"
if defined AGENT_ENGINE_ROOT (
    if exist "%AGENT_ENGINE_ROOT%\\scripts\\{py_rel_win}" set "TARGET_PY=%AGENT_ENGINE_ROOT%\\scripts\\{py_rel_win}"
    if exist "%AGENT_ENGINE_ROOT%\\{py_rel_win}" set "TARGET_PY=%AGENT_ENGINE_ROOT%\\{py_rel_win}"
)
where py >nul 2>&1
if %ERRORLEVEL% equ 0 (
    py -3 "%TARGET_PY%" %*
    exit /b %ERRORLEVEL%
)
where python >nul 2>&1
if %ERRORLEVEL% equ 0 (
    python "%TARGET_PY%" %*
    exit /b %ERRORLEVEL%
)
echo [ERRORE] Python non trovato nel PATH di sistema. >&2
exit /b 1
"""


def ensure_executable(path: Path) -> None:
    """Imposta il bit di esecuzione (+x) su sistemi POSIX."""
    if sys.platform != "win32" and path.exists():
        mode = path.stat().st_mode
        path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def install_shims(
    scripts_dir: Path | None = None,
    bin_dir: Path | None = None,
    home: Path | None = None,
) -> list[str]:
    """Genera e installa tutti gli shim/launcher per la piattaforma corrente."""
    home_dir = home or Path.home()
    target_bin = bin_dir or (home_dir / ".local" / "bin")
    target_bin.mkdir(parents=True, exist_ok=True)

    base_scripts = scripts_dir or Path(__file__).resolve().parents[1]
    installed: list[str] = []

    if sys.platform == "win32":
        # Generazione .cmd su Windows da template unico
        for cmd_name, py_rel in COMMANDS:
            abs_py = base_scripts / py_rel
            cmd_file = target_bin / f"{cmd_name}.cmd"
            py_rel_win = py_rel.replace("/", "\\")
            content = WINDOWS_CMD_TEMPLATE.format(
                py_rel_win=py_rel_win,
                abs_path=str(abs_py),
            )
            cmd_file.write_text(content, encoding="utf-8")
            installed.append(str(cmd_file))
    else:
        # Creazione symlink diretti su Linux/POSIX
        for cmd_name, py_rel in COMMANDS:
            abs_py = base_scripts / py_rel
            if not abs_py.exists() and (base_scripts.parent / py_rel).exists():
                abs_py = base_scripts.parent / py_rel
            ensure_executable(abs_py)
            link_path = target_bin / cmd_name
            if link_path.is_symlink() or link_path.exists():
                link_path.unlink(missing_ok=True)
            try:
                link_path.symlink_to(abs_py)
                installed.append(str(link_path))
            except OSError:
                # Fallback copia diretta
                import shutil
                shutil.copy2(abs_py, link_path)
                ensure_executable(link_path)
                installed.append(str(link_path))

    return installed
