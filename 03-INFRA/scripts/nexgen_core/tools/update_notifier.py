#!/usr/bin/env python3
"""nexgen_core.tools.update_notifier — the update notice you actually see.

Two lanes, one state file:

- terminal (primary): a shell-startup hook runs ``--shell-check``. It reads
  a cache file, never the network: when the cache is stale it refreshes it
  in a detached background process and says nothing this shell. When an
  update is pending it asks once per day per version, ``update now?``,
  and on "yes" hands the terminal to the interactive updater (which shows
  the release notes and asks its own confirmation -- this prompt never
  applies an update by itself).
- graphical (complement): the XDG autostart entry / systemd timer runs the
  default check, which shows a native dialog. Same dismissal state, so the
  two lanes never nag twice for the same version on the same day.

Why this exists in this shape: the pre-2.3.0 notifier only had the
graphical lane, fired from a headless timer where no dialog can ever
appear -- which is why it was requested for a lifetime and never seen.
"""
from __future__ import annotations

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

from nexgen_core.paths import resolve_home, resolve_state_dir

THROTTLE_HOURS = 12
CACHE_STALE_AFTER_HOURS = 12
CACHE_TOO_OLD_TO_NAG_DAYS = 7


def _state_file() -> Path:
    return Path(resolve_state_dir()) / "nexgen" / "update-status.json"


def _today() -> str:
    return datetime.date.today().isoformat()


def _read_state() -> dict:
    try:
        data = json.loads(_state_file().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(patch: dict) -> None:
    try:
        path = _state_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = _read_state()
        data.update(patch)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def _is_throttled() -> bool:
    # Legacy throttle file (pre-2.3.0 GUI lane): honored so an old record
    # still suppresses a same-day repeat after the upgrade.
    legacy = resolve_home() / ".config" / "nexgen" / "last_update_check.json"
    try:
        if legacy.is_file():
            data = json.loads(legacy.read_text(encoding="utf-8"))
            if (time.time() - data.get("timestamp", 0)) < THROTTLE_HOURS * 3600:
                return True
    except (OSError, ValueError):
        pass
    return False


def _record_prompt_time(latest: str) -> None:
    try:
        legacy = resolve_home() / ".config" / "nexgen" / "last_update_check.json"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text(
            json.dumps({"timestamp": time.time(), "latest": latest}, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass
    _write_state({"dismissed": {"version": latest, "day": _today()}})


def _dismissed_today(latest: str) -> bool:
    dismissed = _read_state().get("dismissed")
    return (
        isinstance(dismissed, dict)
        and dismissed.get("version") == latest
        and dismissed.get("day") == _today()
    )


def _prompt_linux(current: str, latest: str, notes_hint: str = "") -> bool:
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False

    text = (
        f"<b>Nuova versione disponibile: {latest}</b> (versione attuale: {current})\n\n"
        "Desideri aggiornare adesso NeXgen Engine?"
    )
    if notes_hint:
        text += f"\n\n{notes_hint}"

    if shutil.which("zenity"):
        cmd = [
            "zenity", "--question",
            "--title=NeXgen Engine Update",
            f"--text={text}",
            "--ok-label=Aggiorna ora",
            "--cancel-label=Più tardi",
            "--width=420",
            "--window-icon=system-software-update"
        ]
        proc = subprocess.run(cmd, check=False)
        return proc.returncode == 0

    if shutil.which("kdialog"):
        cmd = ["kdialog", "--yesno", text.replace("<b>", "").replace("</b>", ""), "--title", "NeXgen Engine Update"]
        proc = subprocess.run(cmd, check=False)
        return proc.returncode == 0

    return False


def _prompt_windows(current: str, latest: str, notes_hint: str = "") -> bool:
    text = (
        f"Nuova versione di NeXgen Engine disponibile: {latest}\n"
        f"(Versione attualmente installata: {current})\n\n"
        "Desideri aggiornare adesso il motore?"
    )
    if notes_hint:
        text += f"\n\n{notes_hint}"
    title = "NeXgen Engine Update"
    MB_YESNO = 0x00000004
    MB_ICONINFORMATION = 0x00000040
    IDYES = 6

    try:
        res = ctypes.windll.user32.MessageBoxW(0, text, title, MB_YESNO | MB_ICONINFORMATION)
        return res == IDYES
    except Exception:
        return False


def _prompt_user(current: str, latest: str, notes_hint: str = "") -> bool:
    if os.name == "nt":
        return _prompt_windows(current, latest, notes_hint)
    return _prompt_linux(current, latest, notes_hint)


def _notify_success(latest: str) -> None:
    if os.name == "nt":
        try:
            ctypes.windll.user32.MessageBoxW(
                0,
                f"NeXgen Engine aggiornato con successo alla versione {latest}!",
                "NeXgen Engine Update",
                0x00000000 | 0x00000040
            )
        except Exception:
            pass
    else:
        if shutil.which("notify-send"):
            subprocess.run([
                "notify-send", "NeXgen Engine",
                f"Aggiornamento a {latest} completato con successo!",
                "--icon=system-software-update"
            ], check=False)
        elif shutil.which("zenity"):
            subprocess.run([
                "zenity", "--info",
                "--title=NeXgen Engine",
                f"--text=Aggiornamento a <b>{latest}</b> completato con successo!",
                "--width=340"
            ], check=False)


def _run_update() -> bool:
    try:
        from nexgen_core.updater import EngineUpdater
        return EngineUpdater.main(["--yes"]) == 0
    except Exception:
        return False


def _notes_hint() -> str:
    return "Note di rilascio: `nexgen update --check`."


def cmd_check(force: bool = False) -> int:
    try:
        from nexgen_core.updater import EngineUpdater
        has_update, current, latest = EngineUpdater.check_updates()
    except Exception as exc:
        print(f"[update-notifier] check failed: {exc}", file=sys.stderr)
        return 1

    if not has_update:
        return 0

    if not force and (_dismissed_today(latest) or _is_throttled()):
        return 0

    _record_prompt_time(latest)
    wants_update = _prompt_user(current, latest, _notes_hint())

    if wants_update:
        ok = _run_update()
        if ok:
            _notify_success(latest)
        else:
            if os.name != "nt" and shutil.which("zenity"):
                subprocess.run(["zenity", "--error", "--title=NeXgen Engine", "--text=Errore durante l'aggiornamento. Riprova con 'nexgen update' dal terminale."], check=False)
            return 1

    return 0


def _newest_tag_ls_remote(engine_repo: str, timeout: int = 20) -> str | None:
    """Newest released semver tag on origin, without touching the checkout.

    Read-only (`ls-remote`): no fetch, no ref moves, safe to run from the
    guard cycle and from background refresh alike. None on any failure
    (offline, no remote, no tags): callers treat that as "unknown", never
    as an error worth surfacing.
    """
    import re

    try:
        proc = subprocess.run(
            ["git", "-C", engine_repo, "ls-remote", "--tags", "--refs", "origin"],
            capture_output=True, text=True, check=False, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    semver = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
    best: tuple[int, int, int] | None = None
    best_tag: str | None = None
    for line in proc.stdout.splitlines():
        fields = line.split()
        if len(fields) != 2 or not fields[1].startswith("refs/tags/"):
            continue
        tag = fields[1].removeprefix("refs/tags/")
        match = semver.fullmatch(tag)
        if not match:
            continue
        version = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
        if best is None or version > best:
            best, best_tag = version, tag
    return best_tag


def refresh_update_cache(engine_repo: str | None = None, timeout: int = 20) -> dict:
    """Refreshes the shell-hook cache. Never raises, never prints: it runs
    from the guard cycle and from detached background refreshes, where any
    noise would land in a log nobody reads or a shell that just started."""
    from nexgen_core.paths import resolve_engine_root

    result: dict = {"checked_at": time.time()}
    try:
        repo = engine_repo
        if repo is None:
            engine_root = resolve_engine_root()
            probe = subprocess.run(
                ["git", "-C", str(engine_root), "rev-parse", "--show-toplevel"],
                capture_output=True, text=True, check=False, timeout=timeout,
            )
            if probe.returncode != 0:
                return result
            repo = probe.stdout.strip()
        current_file = os.path.join(repo, "VERSION")
        try:
            with open(current_file, encoding="utf-8") as handle:
                result["current"] = f"v{handle.read().strip()}"
        except OSError:
            pass
        latest = _newest_tag_ls_remote(repo, timeout=timeout)
        if latest is not None:
            result["latest"] = latest
            result["has_update"] = bool(result.get("current")) and latest.removeprefix("v") != result["current"].removeprefix("v") and _tag_newer(
                latest, result.get("current", "")
            )
        _write_state(result)
    except Exception:
        pass
    return result


def _tag_newer(latest: str, current: str) -> bool:
    import re

    semver = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
    newer = semver.fullmatch(latest.strip())
    older = semver.fullmatch(current.strip())
    if not newer or not older:
        return False
    return tuple(int(g) for g in newer.groups()) > tuple(int(g) for g in older.groups())


def _spawn_background_refresh() -> None:
    """Re-checks origin without holding the shell: detached, silent, cheap."""
    try:
        nexgen = shutil.which("nexgen")
        if not nexgen:
            return
        entry = [nexgen, "tool", "update-notifier", "--refresh-cache"]
        if os.name == "nt":
            creationflags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            subprocess.Popen(
                entry, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL, creationflags=creationflags,
                cwd=str(resolve_home()),
            )
        else:
            subprocess.Popen(
                entry, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL, start_new_session=True,
                cwd=str(resolve_home()),
            )
    except (OSError, ValueError):
        pass


def _cache_fresh(state: dict) -> bool:
    try:
        return (time.time() - float(state.get("checked_at", 0))) < CACHE_STALE_AFTER_HOURS * 3600
    except (TypeError, ValueError):
        return False


def _cache_usable(state: dict) -> bool:
    try:
        age_days = (time.time() - float(state.get("checked_at", 0))) / 86400
    except (TypeError, ValueError):
        return False
    return age_days < CACHE_TOO_OLD_TO_NAG_DAYS and bool(state.get("latest")) and bool(state.get("current"))


def cmd_shell_check() -> int:
    """The shell-startup fast path: file read only, prompt at most once per
    day per version, background refresh when stale. Returns 0 always (a
    shell hook must never break a shell startup)."""
    try:
        return _shell_check()
    except Exception as exc:
        print(f"[update-notifier] shell check failed: {exc}", file=sys.stderr)
        return 0


def _shell_check() -> int:
    state = _read_state()
    if not _cache_fresh(state):
        _spawn_background_refresh()
        if not _cache_usable(state):
            return 0
    if not state.get("has_update"):
        return 0
    current = str(state.get("current", ""))
    latest = str(state.get("latest", ""))
    if not current or not latest or _dismissed_today(latest):
        return 0
    if not sys.stdin.isatty():
        return 0
    try:
        answer = input(
            f"\nNeXgen Engine: {latest} disponibile (installata: {current}). "
            "Note: `nexgen update --check`. Aggiorna ora? [s/N] "
        )
    except EOFError:
        return 0
    _record_prompt_time(latest)
    if answer.strip().lower() in {"s", "si", "y", "yes"}:
        print(f"Avvio `nexgen update` verso {latest}...")
        try:
            os.execvp("nexgen", ["nexgen", "update", "--target", latest.removeprefix("v")])
        except OSError as exc:
            print(f"[update-notifier] cannot launch `nexgen update`: {exc}", file=sys.stderr)
            return 0
    return 0


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


def _append_once(path, block: str) -> bool:
    marker = "NeXgen Engine update notice"
    try:
        existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    except OSError:
        return False
    if marker in existing:
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            if existing and not existing.endswith("\n"):
                handle.write("\n")
            handle.write(block if block.endswith("\n") else block + "\n")
        return True
    except OSError:
        return False


def cmd_install_shell_hook(remove: bool = False, shell: str | None = None) -> int:
    """Installs (or removes) the shell-startup notice. Bash appends a
    guarded block to ~/.bashrc; PowerShell to the user profile. Guarded
    means: interactive shells only, `nexgen` on PATH, marker-checked so a
    second install is a no-op (and removal deletes only our own block)."""
    home = resolve_home()
    targets: list[tuple[str, Path, str]] = [
        ("bash", home / ".bashrc", _BASH_HOOK),
        ("powershell", home / ".config" / "powershell" / "Microsoft.PowerShell_profile.ps1", _POWERSHELL_HOOK),
    ]
    if shell in ("bash", "powershell"):
        targets = [entry for entry in targets if entry[0] == shell]
    marker = "NeXgen Engine update notice"
    rc = 0
    for name, path, block in targets:
        if remove:
            try:
                if path.is_file():
                    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
                    kept = [line for line in lines if marker not in line]
                    # Drop our two-line blocks: marker line plus the nexgen line around it.
                    cleaned = [line for line in kept if "nexgen tool update-notifier --shell-check" not in line]
                    if len(cleaned) != len(lines):
                        path.write_text("".join(cleaned), encoding="utf-8")
                        print(f"[shell-hook] removed from {path}")
                    else:
                        print(f"[shell-hook] not present in {path}")
                else:
                    print(f"[shell-hook] not present in {path}")
            except OSError as exc:
                print(f"[shell-hook] cannot update {path}: {exc}", file=sys.stderr)
                rc = 1
            continue
        if _append_once(path, block):
            print(f"[shell-hook] installed for {name} in {path} (restart the shell to take effect)")
        else:
            print(f"[shell-hook] already present for {name} ({path})")
    return rc


def cmd_install_autostart() -> int:
    home = resolve_home()
    if os.name != "nt":
        nexgen_bin = home / ".local" / "bin" / "nexgen"
        exec_cmd = str(nexgen_bin) if nexgen_bin.is_file() else "nexgen"
        autostart_dir = home / ".config" / "autostart"
        autostart_dir.mkdir(parents=True, exist_ok=True)
        desktop_file = autostart_dir / "nexgen-update-check.desktop"
        content = f"""[Desktop Entry]
Type=Application
Name=NeXgen Update Check
Comment=Verifica aggiornamenti disponibili per NeXgen Engine
Exec={exec_cmd} tool update-notifier
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=120
"""
        desktop_file.write_text(content, encoding="utf-8")
        print(f"[autostart] Linux XDG Autostart creato: {desktop_file}")

        systemd_dir = home / ".config" / "systemd" / "user"
        systemd_dir.mkdir(parents=True, exist_ok=True)
        service_file = systemd_dir / "nexgen-update-check.service"
        timer_file = systemd_dir / "nexgen-update-check.timer"

        service_content = f"""[Unit]
Description=NeXgen Engine Update Check
After=graphical-session.target network-online.target

[Service]
Type=oneshot
ExecStart={exec_cmd} tool update-notifier
Environment=DISPLAY=:0
Environment=XAUTHORITY=%h/.Xauthority

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
        service_file.write_text(service_content, encoding="utf-8")
        timer_file.write_text(timer_content, encoding="utf-8")
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
        subprocess.run(["systemctl", "--user", "enable", "--now", "nexgen-update-check.timer"], check=False)
        print(f"[autostart] Systemd user timer attivato: {timer_file}")
    else:
        print("[autostart] Su Windows: posiziona un collegamento a 'nexgen tool update-notifier' nella cartella Esecuzione automatica (Startup).")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nexgen tool update-notifier", description="NeXgen Engine update notifier with native UI prompt.")
    parser.add_argument("--force", action="store_true", help="ignora il throttle temporale e mostra il prompt se disponibile")
    parser.add_argument("--demo", action="store_true", help="simula il dialogo con una versione fittizia per test")
    parser.add_argument("--install-autostart", action="store_true", help="configura l'avvio automatico all'accesso utente")
    parser.add_argument("--shell-check", action="store_true", help="controllo veloce per l'avvio della shell: legge la cache, chiede al massimo una volta al giorno")
    parser.add_argument("--refresh-cache", action="store_true", help="aggiorna la cache degli update in silenzio (uso interno: hook e guard)")
    parser.add_argument("--install-shell-hook", action="store_true", help="installa l'avviso all'avvio della shell (bash + powershell)")
    parser.add_argument("--remove", action="store_true", help="con --install-shell-hook: rimuove l'avviso invece di installarlo")
    parser.add_argument("--shell", choices=["bash", "powershell"], default=None, help="con --install-shell-hook: solo questa shell")
    args = parser.parse_args(argv)

    if args.install_autostart:
        return cmd_install_autostart()

    if args.install_shell_hook:
        return cmd_install_shell_hook(remove=args.remove, shell=args.shell)

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
