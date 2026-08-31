"""nexgen_core.tools.update_notifier — Interactive update notifier for NeXgen Engine."""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time

from nexgen_core.paths import resolve_home

HOME = resolve_home()
THROTTLE_FILE = HOME / ".config" / "nexgen" / "last_update_check.json"
THROTTLE_HOURS = 12


def _is_throttled() -> bool:
    if not THROTTLE_FILE.is_file():
        return False
    try:
        data = json.loads(THROTTLE_FILE.read_text(encoding="utf-8"))
        last_time = data.get("timestamp", 0)
        return (time.time() - last_time) < (THROTTLE_HOURS * 3600)
    except Exception:
        return False


def _record_prompt_time(latest: str) -> None:
    try:
        THROTTLE_FILE.parent.mkdir(parents=True, exist_ok=True)
        THROTTLE_FILE.write_text(
            json.dumps({"timestamp": time.time(), "latest": latest}, indent=2) + "\n",
            encoding="utf-8"
        )
    except Exception:
        pass


def _prompt_linux(current: str, latest: str) -> bool:
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False

    text = (
        f"<b>Nuova versione disponibile: {latest}</b> (versione attuale: {current})\n\n"
        "Desideri aggiornare adesso NeXgen Engine?"
    )

    if shutil.which("zenity"):
        cmd = [
            "zenity", "--question",
            "--title=NeXgen Engine Update",
            f"--text={text}",
            "--ok-label=Aggiorna ora",
            "--cancel-label=Più tardi",
            "--width=380",
            "--window-icon=system-software-update"
        ]
        proc = subprocess.run(cmd, check=False)
        return proc.returncode == 0

    if shutil.which("kdialog"):
        cmd = ["kdialog", "--yesno", text.replace("<b>", "").replace("</b>", ""), "--title", "NeXgen Engine Update"]
        proc = subprocess.run(cmd, check=False)
        return proc.returncode == 0

    return False


def _prompt_windows(current: str, latest: str) -> bool:
    text = (
        f"Nuova versione di NeXgen Engine disponibile: {latest}\n"
        f"(Versione attualmente installata: {current})\n\n"
        "Desideri aggiornare adesso il motore?"
    )
    title = "NeXgen Engine Update"
    MB_YESNO = 0x00000004
    MB_ICONINFORMATION = 0x00000040
    IDYES = 6

    try:
        res = ctypes.windll.user32.MessageBoxW(0, text, title, MB_YESNO | MB_ICONINFORMATION)
        return res == IDYES
    except Exception:
        return False


def _prompt_user(current: str, latest: str) -> bool:
    if os.name == "nt":
        return _prompt_windows(current, latest)
    return _prompt_linux(current, latest)


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


def cmd_check(force: bool = False) -> int:
    try:
        from nexgen_core.updater import EngineUpdater
        has_update, current, latest = EngineUpdater.check_updates()
    except Exception as exc:
        print(f"[update-notifier] check failed: {exc}", file=sys.stderr)
        return 1

    if not has_update:
        return 0

    if not force and _is_throttled():
        return 0

    _record_prompt_time(latest)
    wants_update = _prompt_user(current, latest)

    if wants_update:
        ok = _run_update()
        if ok:
            _notify_success(latest)
        else:
            if os.name != "nt" and shutil.which("zenity"):
                subprocess.run(["zenity", "--error", "--title=NeXgen Engine", "--text=Errore durante l'aggiornamento. Riprova con 'nexgen update' dal terminale."], check=False)
            return 1

    return 0


def cmd_install_autostart() -> int:
    if os.name != "nt":
        autostart_dir = HOME / ".config" / "autostart"
        autostart_dir.mkdir(parents=True, exist_ok=True)
        desktop_file = autostart_dir / "nexgen-update-check.desktop"
        content = """[Desktop Entry]
Type=Application
Name=NeXgen Update Check
Comment=Verifica aggiornamenti disponibili per NeXgen Engine
Exec=nexgen tool update-notifier
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=120
"""
        desktop_file.write_text(content, encoding="utf-8")
        print(f"[autostart] Linux XDG Autostart creato: {desktop_file}")

        systemd_dir = HOME / ".config" / "systemd" / "user"
        systemd_dir.mkdir(parents=True, exist_ok=True)
        service_file = systemd_dir / "nexgen-update-check.service"
        timer_file = systemd_dir / "nexgen-update-check.timer"

        service_content = """[Unit]
Description=NeXgen Engine Update Check
After=graphical-session.target network-online.target

[Service]
Type=oneshot
ExecStart=%h/.local/bin/nexgen tool update-notifier
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
    args = parser.parse_args(argv)

    if args.install_autostart:
        return cmd_install_autostart()

    if args.demo:
        wants_update = _prompt_user("v2.1.4", "v2.1.5 (Demo)")
        print(f"[demo] Risposta utente: {'Aggiorna' if wants_update else 'Più tardi'}")
        if wants_update:
            _notify_success("v2.1.5 (Demo)")
        return 0

    return cmd_check(force=args.force)
