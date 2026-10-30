"""The Windows logon fallback that keeps the guard running without Task Scheduler.

One job: publish the generated wrapper into the Startup folder when `schtasks.exe`
refuses the logon trigger, rotating the previous copy out of the folder Windows
executes at logon. It lives beside the scheduler because that file is past its
size ceiling, and because this is the one part of it that a machine without Task
Scheduler exercises on its own.
"""
from __future__ import annotations

import shutil
from pathlib import Path

#: How many previous Startup fallback scripts survive rotation.
STARTUP_FALLBACK_KEEP = 3


def _startup_backup_dir(startup_vbs: Path) -> Path:
    """Sibling of the Startup folder: Windows never executes it."""
    return startup_vbs.parent.parent / "NeXgen-Backup"


def _remove_stray_startup_backups(startup_vbs: Path) -> None:
    """Drops previous-version backups from inside the Startup folder.

    Every file in there runs at logon, including `.bak` copies, so
    anything matching our backup stem is a stray and goes away.
    Best-effort: a locked file is retried on the next cycle.
    """
    for stray in sorted(startup_vbs.parent.glob(startup_vbs.name + "*.bak")):
        try:
            stray.unlink()
        except OSError:  # nexgen: allow reason="a locked stray backup is retried on the next cycle; the logon fallback is still published"
            pass


def _install_startup_fallback(startup_dir: Path, content: str, heartbeat_ok: bool, log) -> bool:
    """Publishes the Startup-folder logon fallback, rotating its backups.

    Writes only when the content changed; keeps the newest
    STARTUP_FALLBACK_KEEP previous scripts in a sibling directory
    Windows never executes, so a version flap mid-update cannot leave
    runnable `.bak` copies behind in the Startup folder.
    """
    from nexgen_core.files import atomic_write_text, backup_file
    startup_vbs = startup_dir / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "KnowledgeVault Agent Sync.vbs"
    backup_dir = _startup_backup_dir(startup_vbs)
    startup_vbs.parent.mkdir(parents=True, exist_ok=True)
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        _remove_stray_startup_backups(startup_vbs)
        if startup_vbs.exists() or startup_vbs.is_symlink():
            existing = startup_vbs.read_bytes()
            if existing == content.encode("utf-8"):
                return heartbeat_ok
            backup = backup_file(startup_vbs, tag="startup", keep=STARTUP_FALLBACK_KEEP)
            if backup is not None:
                try:
                    relocated = backup_dir / backup.name
                    shutil.move(str(backup), str(relocated))
                except OSError as exc:
                    log(f"startup: cannot relocate logon backup ({type(exc).__name__}); previous script preserved")
                    try:
                        backup.unlink()
                    except OSError:  # nexgen: allow reason="the preserved copy is already named in the log; a leftover backup must not fail the logon fallback"
                        pass
                    return heartbeat_ok
                backups = sorted(backup_dir.glob(startup_vbs.name + ".pre-startup-*.bak"))
                for old in backups[: max(len(backups) - STARTUP_FALLBACK_KEEP, 0)]:
                    try:
                        old.unlink()
                    except OSError:  # nexgen: allow reason="an old backup that cannot be removed is retried on the next cycle and never blocks the install"
                        pass
                log(f"startup: kept previous logon script as {relocated}")
        atomic_write_text(startup_vbs, content)
    except OSError as exc:
        log(f"startup: cannot safely publish fallback ({type(exc).__name__}); previous script preserved")
        return heartbeat_ok
    log(f"startup: fallback logon installed {startup_vbs}")
    return heartbeat_ok
