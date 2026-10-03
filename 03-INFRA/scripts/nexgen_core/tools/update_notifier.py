#!/usr/bin/env python3
"""nexgen_core.tools.update_notifier — facade only.

Each lane lives in its owner module; this file keeps the import path
(`from nexgen_core.tools import update_notifier`) and re-exports every
public and test-used name, plus the CLI dispatcher (main).
"""
from __future__ import annotations

from .notifier_boot import (
    _BASH_HOOK,
    _FISH_HOOK,
    _POWERSHELL_HOOK,
    _UPDATE_CHECK_TASK,
    _append_once,
    _boot_inventory,
    _ensure_posix_boot_check,
    _ensure_windows_boot_check,
    _nexgen_cmd,
    _notify_passive,
    _notify_passive_windows,
    _posix_shell_targets,
    _publish_inventory,
    _remove_autostart,
    _remove_windows_fallback,
    _run_quiet,
    _windows_startup_copy,
    _windows_task_runs_notifier,
    _windows_vbs_content,
    _write_if_different,
    _write_text_if_different,
    cmd_boot,
    cmd_install_autostart,
    cmd_install_shell_hook,
    ensure_boot_check,
    ensure_shell_hook,
)
from .notifier_prompt import (
    _check_skills_gui,
    _logo_path,
    _notes_hint,
    _notify_success,
    _prompt_linux,
    _prompt_user,
    _prompt_windows,
    _run_update,
    _shell_check,
    _shell_check_engine,
    _shell_check_skills,
    cmd_check,
    cmd_shell_check,
)
from .notifier_skills import (
    _applied_file,
    _batch_once_daily,
    _confirm_skills_shown,
    _held_once_daily,
    _short_skill_name,
    _skills_notice,
    _take_fresh_applied,
)
from .notifier_state import (
    CACHE_STALE_AFTER_HOURS,
    CACHE_TOO_OLD_TO_NAG_DAYS,
    SKILLS_REPORT_NAME,
    THROTTLE_HOURS,
    _cache_fresh,
    _cache_usable,
    _dismissed_today,
    _is_throttled,
    _newest_tag_ls_remote,
    _read_state,
    _record_prompt_time,
    _record_skills_dismissal,
    _skills_dismissed,
    _skills_report_file,
    _spawn_background_refresh,
    _state_file,
    _tag_newer,
    _today,
    _write_state,
    refresh_update_cache,
)

# Stdlib handles re-exported so tests can patch via this module
# (patching notifier.subprocess.run patches the shared stdlib object).
import argparse
import ctypes
import datetime
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

__all__ = ["main"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nexgen tool update-notifier", description="NeXgen Engine update notifier with native UI prompt.")
    parser.add_argument("--force", action="store_true", help="ignora il throttle temporale e mostra il prompt se disponibile")
    parser.add_argument("--demo", action="store_true", help="simula il dialogo con una versione fittizia per test")
    parser.add_argument("--install-autostart", action="store_true", help="configura l'avvio automatico all'accesso utente")
    parser.add_argument("--boot", action="store_true", help="controllo silenzioso all'avvio: aggiorna le cache e manda l'inventario passivo")
    parser.add_argument("--shell-check", action="store_true", help="controllo veloce per l'avvio della shell: legge la cache, chiede al massimo una volta al giorno")
    parser.add_argument("--refresh-cache", action="store_true", help="aggiorna la cache degli update in silenzio (uso interno: hook e guard)")
    parser.add_argument("--install-shell-hook", action="store_true", help="installa l'avviso all'avvio della shell (bash/zsh/fish + powershell)")
    parser.add_argument("--remove", action="store_true", help="con --install-shell-hook: rimuove l'avviso invece di installarlo")
    parser.add_argument("--shell", choices=["bash", "zsh", "fish", "powershell"], default=None, help="con --install-shell-hook: solo questa shell")
    args = parser.parse_args(argv)

    if args.install_autostart:
        return cmd_install_autostart(remove=args.remove)

    if args.install_shell_hook:
        return cmd_install_shell_hook(remove=args.remove, shell=args.shell)

    if args.boot:
        return cmd_boot()

    if args.refresh_cache:
        refresh_update_cache()
        return 0

    if args.shell_check:
        return cmd_shell_check()

    if args.demo:
        wants_update = _prompt_user("v2.1.4", "v2.1.5 (Demo)", _notes_hint())
        print(f"[demo] Risposta utente: {'Aggiorna' if wants_update else 'Più tardi'}")
        if wants_update:
            _notify_success("v2.1.5 (Demo)")
        return 0

    return cmd_check(force=args.force)


if __name__ == "__main__":
    sys.exit(main())