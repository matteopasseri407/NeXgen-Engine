"""update-notifier shell hook + boot lanes + passive notify. Owned here, re-exported by update_notifier for compat."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from nexgen_core.i18n import t
from nexgen_core.paths import resolve_home


from .notifier_state import _read_state, refresh_update_cache
from .notifier_skills import _short_skill_name

def _call_logo_path() -> str:
    try:
        from . import update_notifier as _fac

        func = getattr(_fac, "_logo_path", None)
        if func is not None:
            # facade re-exports prompt's impl when unpatched; patched lambda differs
            from .notifier_prompt import _logo_path as _local

            if func is not _local:
                return func()
    except ImportError:
        pass
    from .notifier_prompt import _logo_path as _local2

    return _local2()



_BASH_HOOK = """# NeXgen Engine update notice (managed: `nexgen tool update-notifier --install-shell-hook --remove` to stop).
if [[ $- == *i* ]] && command -v nexgen >/dev/null 2>&1; then
  nexgen tool update-notifier --shell-check
fi
"""

_POWERSHELL_HOOK = """
# NeXgen Engine update notice (managed: remove this block to stop).
if ($Host.Name -eq 'ConsoleHost' -and (Get-Command nexgen -ErrorAction SilentlyContinue)) {
  nexgen tool update-notifier --shell-check
}
"""

_FISH_HOOK = """# NeXgen Engine update notice (managed: `nexgen tool update-notifier --install-shell-hook --remove` to stop).
if status is-interactive; and command -v nexgen >/dev/null 2>&1
  nexgen tool update-notifier --shell-check
end
"""


def _append_once(path: Path, block: str) -> bool | None:
    """True for a write, False if present, None when the write failed."""
    from nexgen_core.files import write_text_if_changed

    marker = "NeXgen Engine update notice"
    try:
        existing = path.read_text(encoding="utf-8") if path.is_file() else ""
        if marker in existing:
            return False
        separator = "\n" if existing and not existing.endswith("\n") else ""
        return write_text_if_changed(path, existing + separator + block, tag="shell-hook")
    except OSError:
        return None


def _posix_shell_targets(home: Path) -> list[tuple[str, Path, str]]:
    """Shell hook targets on POSIX: bash always, zsh/fish only when used.

    Writing a .zshrc or fish config for a shell the user never opened
    would create files that did not exist; a missing rc means that shell
    is not in use here.
    """
    targets = [("bash", home / ".bashrc", _BASH_HOOK)]
    zshrc = home / ".zshrc"
    if zshrc.is_file():
        targets.append(("zsh", zshrc, _BASH_HOOK))
    fish_cfg = home / ".config" / "fish" / "config.fish"
    if fish_cfg.is_file():
        targets.append(("fish", fish_cfg, _FISH_HOOK))
    return targets


def cmd_install_shell_hook(remove: bool = False, shell: str | None = None, *, home: Path | None = None) -> int:
    """Installs (or removes) the shell-startup notice. Bash and zsh share
    the guarded block (~/.bashrc, ~/.zshrc when it exists); fish gets its
    own syntax; PowerShell to the user profile. Guarded means:
    interactive shells only, `nexgen` on PATH, marker-checked so a
    second install is a no-op (and removal deletes only our own block)."""
    home = resolve_home(home)
    targets = _posix_shell_targets(home) if os.name != "nt" or shell in ("bash", "zsh", "fish") else []
    targets.append(("powershell", home / ".config" / "powershell" / "Microsoft.PowerShell_profile.ps1", _POWERSHELL_HOOK))
    if shell in ("bash", "zsh", "fish", "powershell"):
        targets = [entry for entry in targets if entry[0] == shell]
    marker = "NeXgen Engine update notice"
    rc = 0
    for name, path, block in targets:
        if remove:
            try:
                if path.is_file():
                    from nexgen_core.files import write_text_if_changed

                    existing = path.read_text(encoding="utf-8")
                    cleaned = existing.replace(block, "")
                    if cleaned != existing:
                        write_text_if_changed(path, cleaned, tag="shell-hook")
                        print(f"[shell-hook] removed from {path}")
                    elif marker in existing:
                        print(t("Shell hook: unrecognized managed block in {path}; preserved.", path=path), file=sys.stderr)
                        rc = 1
                    else:
                        print(f"[shell-hook] not present in {path}")
                else:
                    print(f"[shell-hook] not present in {path}")
            except OSError as exc:
                print(f"[shell-hook] cannot update {path}: {exc}", file=sys.stderr)
                rc = 1
            continue
        changed = _append_once(path, block)
        if changed is None:
            print(t("Shell hook: cannot update {path}.", path=path), file=sys.stderr)
            rc = 1
        elif changed:
            print(f"[shell-hook] installed for {name} in {path} (restart the shell to take effect)")
        else:
            print(f"[shell-hook] already present for {name} ({path})")
    return rc


def _nexgen_cmd(home: Path) -> str:
    """The launcher the boot entries invoke, preferring the installed shim."""
    suffix = ".cmd" if os.name == "nt" else ""
    shim = home / ".local" / "bin" / f"nexgen{suffix}"
    return str(shim) if shim.is_file() else "nexgen"


def _write_if_different(path: Path, content: str) -> bool | None:
    """True when written, False when already correct, None on error.

    None and False both read falsy, so callers must check identity
    (`is None`) before deciding a lane is installed. Single owner for the
    write itself is `nexgen_core.files.atomic_write_text`.
    """
    try:
        if path.is_file() and path.read_text(encoding="utf-8") == content:
            return False
    except OSError:
        pass
    try:
        from nexgen_core.files import atomic_write_text

        atomic_write_text(path, content)
        return True
    except OSError:
        return None


def _run_quiet(argv: list[str], timeout: int = 60) -> tuple[int, str]:
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, check=False, timeout=timeout)
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)


_UPDATE_CHECK_TASK = "NeXgen Engine Update Check"


_UPDATE_CHECK_TASK = "NeXgen Engine Update Check"


def _windows_vbs_content(exec_cmd: str) -> str:
    """Hidden-runner script. The launcher path is quoted VBScript-style
    (doubled quotes): an install under `C:\\Users\\First Last` must run
    instead of failing silently at logon."""
    quoted = exec_cmd.replace('"', '""')
    return (
        'Set shell = CreateObject("WScript.Shell")\n'
        f'shell.Run """{quoted}"" tool update-notifier --boot", 0, False\n'
    )


def _windows_task_runs_notifier(vbs_path: str) -> bool:
    """True when the logon task already invokes this exact wrapper.

    The task XML carries the `wscript.exe "<wrapper>"` command, not the
    launcher: matching the launcher instead would recreate the task on
    every guard run.
    """
    rc, out = _run_quiet(["schtasks.exe", "/Query", "/TN", _UPDATE_CHECK_TASK, "/XML"])
    return rc == 0 and vbs_path in out


def _windows_startup_copy(appdata: str | None, home: str) -> str:
    base = appdata or os.path.join(home, "AppData", "Roaming")
    return os.path.join(base, "Microsoft", "Windows", "Start Menu",
                        "Programs", "Startup", "NeXgen Update Check.vbs")


def _remove_windows_fallback(home: str) -> bool:
    """Deletes the Startup fallback copy, if any. Returns True when
    nothing executable is left behind there."""
    dest = _windows_startup_copy(os.environ.get("APPDATA"), home)
    try:
        if os.path.isfile(dest):
            os.remove(dest)
            return True
        return True
    except OSError:
        return os.path.isfile(dest) is False


def _ensure_windows_boot_check(home: str) -> list[str]:
    """Logon-time update check on Windows: scheduled task, Startup fallback.

    String paths throughout (no pathlib): the logic stays testable
    anywhere, while on real Windows the same calls use native semantics.
    """
    notes: list[str] = []
    shim = os.path.join(home, ".local", "bin", "nexgen.cmd")
    exec_cmd = shim if os.path.isfile(shim) else "nexgen"
    state_dir = os.path.join(home, ".local", "state")
    vbs = os.path.join(state_dir, "nexgen-update-check-hidden.vbs")
    wrote = _write_text_if_different(vbs, _windows_vbs_content(exec_cmd))
    if wrote is None and not os.path.isfile(vbs):
        return ["[WARN] wrapper not writable, logon task skipped (nothing points at thin air)"]
    if wrote:
        notes.append(f"[autostart] hidden wrapper updated: {vbs}")
    if _windows_task_runs_notifier(vbs):
        _remove_windows_fallback(home)
        if not notes:
            return ["[autostart] logon task already active"]
        return notes
    rc, out = _run_quiet([
        "schtasks.exe", "/Create", "/TN", _UPDATE_CHECK_TASK, "/SC", "ONLOGON",
        "/TR", f'wscript.exe "{vbs}"', "/F",
    ])
    if rc == 0:
        _remove_windows_fallback(home)
        notes.append("[autostart] logon task installed via schtasks.exe")
        return notes
    dest = _windows_startup_copy(os.environ.get("APPDATA"), home)
    try:
        parent = os.path.dirname(dest)
        os.makedirs(parent, exist_ok=True)
        shutil.copy2(vbs, dest)
        notes.append(f"[autostart] logon fallback installed: {dest}")
    except OSError as exc:
        notes.append(f"[WARN] logon task failed ({out.strip()}) and Startup fallback failed ({exc})")
    return notes


def _write_text_if_different(path_str: str, content: str) -> bool | None:
    """String-path twin of _write_if_different for the Windows lane.

    Stays on `os`/`str` paths on purpose (no pathlib): the Windows lane is
    exercised on Linux with `os.name` mocked to `nt`, where `Path` would
    become an uninstallable `WindowsPath`. Atomic via temp+rename, same
    crash safety as `files.atomic_write_text`.
    """
    try:
        with open(path_str, encoding="utf-8") as handle:
            if handle.read() == content:
                return False
    except OSError:
        pass
    try:
        import tempfile

        parent = os.path.dirname(path_str)
        if parent:
            os.makedirs(parent, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix="nexgen-vbs.", suffix=".tmp", dir=parent or None)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                try:
                    os.fsync(handle.fileno())
                except OSError:
                    pass
            os.replace(tmp_name, path_str)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        return True
    except OSError:
        return None


def _ensure_posix_boot_check(home: Path) -> list[str]:
    """Login-time dialog plus boot timer on Linux: XDG autostart entry and
    a systemd user timer (OnBootSec covers every boot with linger)."""
    notes: list[str] = []
    exec_cmd = _nexgen_cmd(home)
    desktop_file = home / ".config" / "autostart" / "nexgen-update-check.desktop"
    desktop = f"""[Desktop Entry]
Type=Application
Name=NeXgen Update Check
Comment=Verifica aggiornamenti disponibili per NeXgen Engine
Exec={exec_cmd} tool update-notifier --boot
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=120
"""
    service_content = f"""[Unit]
Description=NeXgen Engine Update Check
After=graphical-session.target network-online.target

[Service]
Type=oneshot
# Stesso PATH del servizio inventario: senza le bin dei CLI i probe
# vedrebbero una macchina piu' povera di quella che e'.
Environment=PATH=%h/.opencode/bin:%h/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart={exec_cmd} tool update-notifier --boot
Environment=DISPLAY=:0
Environment=XAUTHORITY=%h/.Xauthority
TimeoutStartSec=300

[Install]
WantedBy=default.target
"""
    timer_content = """[Unit]
Description=Timer per verifica giornaliera aggiornamenti NeXgen Engine

[Timer]
OnBootSec=3min
OnUnitActiveSec=12h
Persistent=true

[Install]
WantedBy=timers.target
"""
    changed = _write_if_different(desktop_file, desktop)
    service_file = home / ".config" / "systemd" / "user" / "nexgen-update-check.service"
    timer_file = home / ".config" / "systemd" / "user" / "nexgen-update-check.timer"
    wrote_service = _write_if_different(service_file, service_content)
    wrote_timer = _write_if_different(timer_file, timer_content)
    if wrote_service is None or wrote_timer is None:
        missing = [str(p) for p, w in ((service_file, wrote_service), (timer_file, wrote_timer))
                   if w is None and not p.is_file()]
        if missing:
            notes.append(f"[WARN] boot entries not writable ({', '.join(missing)}), timer skipped")
            return notes
        notes.append("[WARN] boot entries could not be verified, enabling anyway")
    changed = bool(changed or wrote_service or wrote_timer)
    if changed:
        notes.append(f"[autostart] boot entries written under {home}")
    if not shutil.which("systemctl"):
        notes.append("[autostart] systemctl not found: files written but timer not enabled")
        return notes
    if changed:
        _run_quiet(["systemctl", "--user", "daemon-reload"], timeout=30)
    rc, out = _run_quiet(["systemctl", "--user", "is-enabled", "nexgen-update-check.timer"], timeout=30)
    if "enabled" not in out:
        rc, out = _run_quiet(["systemctl", "--user", "enable", "--now", "nexgen-update-check.timer"], timeout=60)
        if rc == 0:
            notes.append("[autostart] boot timer enabled")
        else:
            notes.append(f"[WARN] timer not enabled ({out.strip()}); headless machines need `loginctl enable-linger $USER`")
    elif not notes:
        notes.append("[autostart] boot timer already enabled")
    return notes


def ensure_boot_check(home: Path | str | None = None) -> list[str]:
    """Idempotent boot-time update check for this machine. Never raises:
    a failed lane is a note, not a broken sync."""
    try:
        if os.name == "nt":
            base = (os.fspath(home) if home is not None
                    else (os.environ.get("NEXGEN_HOME") or os.path.expanduser("~")))
            return _ensure_windows_boot_check(base)
        resolved = resolve_home(home if home is None or isinstance(home, Path) else Path(home))
        return _ensure_posix_boot_check(resolved)
    except Exception as exc:  # noqa: BLE001 - notifier never fails the shell
        return [f"[WARN] boot check not ensured ({exc})"]


def ensure_shell_hook(home: Path | None = None) -> list[str]:
    """Idempotent shell-startup notice. Never raises."""
    try:
        marker = "NeXgen Engine update notice"
        resolved = resolve_home(home)
        if os.name == "nt":
            targets = [resolved / ".config" / "powershell" / "Microsoft.PowerShell_profile.ps1"]
        else:
            targets = [path for _, path, _ in _posix_shell_targets(resolved)]
        missing = [p for p in targets
                   if not p.is_file() or marker not in p.read_text(encoding="utf-8", errors="replace")]
        if not missing:
            return ["[shell-hook] already present"]
        rc = cmd_install_shell_hook(home=resolved)
        if rc == 0:
            return ["[shell-hook] installed for this machine's shells"]
        return ["[WARN] shell hook installation returned an error"]
    except Exception as exc:  # noqa: BLE001 - notifier never fails the shell
        return [f"[WARN] shell hook not ensured ({exc})"]


def cmd_install_autostart(remove: bool = False) -> int:
    home = resolve_home()
    if remove:
        return _remove_autostart(home)
    if os.name != "nt":
        print("\n".join(_ensure_posix_boot_check(home)))
        return 0
    print("\n".join(_ensure_windows_boot_check(home)))
    return 0


def _remove_autostart(home: Path) -> int:
    if os.name == "nt":
        rc, _ = _run_quiet(["schtasks.exe", "/Delete", "/TN", _UPDATE_CHECK_TASK, "/F"])
        print("[autostart] logon task removed" if rc == 0 else "[autostart] no logon task present")
        leftover = not _remove_windows_fallback(os.fspath(home))
        if leftover:
            print("[WARN] Startup fallback copy could not be removed, delete it by hand")
        return 0
    removed = []
    for path in (home / ".config" / "autostart" / "nexgen-update-check.desktop",
                 home / ".config" / "systemd" / "user" / "nexgen-update-check.service",
                 home / ".config" / "systemd" / "user" / "nexgen-update-check.timer"):
        try:
            if path.is_file():
                path.unlink()
                removed.append(str(path))
        except OSError:
            pass
    _run_quiet(["systemctl", "--user", "disable", "--now", "nexgen-update-check.timer"], timeout=60)
    print("[autostart] removed: " + (", ".join(removed) if removed else "nothing present"))
    return 0


def cmd_boot() -> int:
    """Boot-time lane: silent check, passive inventory, zero questions.

    Runs at every boot and login (systemd timer, XDG autostart, Windows
    logon task). It refreshes the engine cache, re-reads third-party pins
    upstream, and delivers one passive notification with the inventory:
    engine update pending, if any, plus every stale skill/MCP pin. Silent
    when everything is up to date. Never prompts, never applies.
    """
    try:
        lines = _boot_inventory()
    except Exception as exc:  # noqa: BLE001 - notifier never fails the shell
        print(f"[update-notifier] boot check failed: {exc}", file=sys.stderr)
        return 0
    if not lines:
        _publish_inventory()
        return 0
    message = "NeXgen aggiornamenti:\n" + "\n".join(f"- {line}" for line in lines)
    print(message)
    _notify_passive(message)
    _publish_inventory()
    return 0


def _boot_inventory() -> list[str]:
    """Fresh inventory: engine tag plus live third-party pins."""
    lines: list[str] = []
    try:
        refresh_update_cache()
    except Exception:  # noqa: BLE001 - notifier never fails the shell
        pass
    try:
        state = _read_state()
        current = str(state.get("current") or "")
        latest = str(state.get("latest") or "")
        if state.get("has_update") and current and latest:
            lines.append(f"motore {current} -> {latest} (`nexgen update --check`)")
    except Exception:  # noqa: BLE001 - notifier never fails the shell
        pass
    try:
        from nexgen_core.depwatch import run_depwatch

        result = run_depwatch()
        stale = sorted({f.what for f in result.findings if f.stale})
        lines.extend(f"{_short_skill_name(what)} da aggiornare" for what in stale[:12])
        if len(stale) > 12:
            lines.append(f"altri {len(stale) - 12} nel report di stato")
    except Exception:  # noqa: BLE001 - notifier never fails the shell
        pass
    return lines


def _publish_inventory() -> None:
    """Sends this host's CLI inventory to the governor, best effort.

    The publisher lives in the vault (private setup, per-host probes);
    end-to-end inventory works only where that script exists. Absence is
    logged at debug (not silent, not a boot failure): without this line an
    operator reads 'boot ok' as 'governor updated'.
    Never fails the boot.
    """
    try:
        import logging

        from nexgen_core.paths import resolve_vault_data

        script = resolve_vault_data() / "03-INFRA" / "governor-publish-inventory.py"
        if not script.is_file():
            logging.getLogger(__name__).debug("governor inventory skipped: no vault script at %s", script)
            return
        proc = subprocess.run(
            [sys.executable, str(script), "--write", "--push"],
            capture_output=True, text=True, check=False, timeout=280,
        )
        tail = ((proc.stderr or "") + (proc.stdout or "")).strip().splitlines()[-3:]
        print("[boot] inventario governor: " + (" | ".join(tail) if tail else "nessuna risposta"))
    except Exception as exc:  # noqa: BLE001 - notifier never fails the shell
        print(f"[boot] inventario governor non inviato ({exc})")


def _notify_passive(message: str) -> None:
    """One passive desktop note, never a question, never blocking."""
    try:
        if os.name == "nt":
            _notify_passive_windows(message)
            return
        if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            return
        if shutil.which("notify-send"):
            subprocess.run(["notify-send", "NeXgen Engine", message,
                            f"--icon={_call_logo_path() or 'system-software-update'}"], check=False)
    except Exception:  # noqa: BLE001 - notifier never fails the shell
        pass


def _notify_passive_windows(message: str) -> None:
    """Windows toast when BurntToast exists, otherwise nothing at all:
    a boot service must never pop a blocking dialog."""
    try:
        probe = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-Module -ListAvailable -Name BurntToast | Select-Object -First 1"],
            capture_output=True, text=True, check=False, timeout=30,
        )
        if "BurntToast" not in (probe.stdout or ""):
            return
        text = message.replace("'", "''")[:380]
        toast = f"New-BurntToastNotification -Text 'NeXgen Engine', '{text}'"
        if _call_logo_path():
            toast += f" -AppLogo '{_call_logo_path()}'"
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", toast],
            check=False, timeout=30,
        )
    except Exception:  # noqa: BLE001 - notifier never fails the shell
        pass
