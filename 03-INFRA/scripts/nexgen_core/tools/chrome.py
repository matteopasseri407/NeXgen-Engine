#!/usr/bin/env python3
"""Gestione del browser Chrome condiviso con porta di debug locale (CDP 9222).

Contratto:
- agent-chrome [args...]: apre la finestra Chrome o passa gli argomenti al browser esistente.
- agent-chrome --ensure: esce con 0 se CDP su 127.0.0.1:9222 risponde già, altrimenti avvia Chrome.
- agent-chrome --heal: riavvia Chrome se il processo è rimasto aperto senza la porta CDP.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

CDP_URL = "http://127.0.0.1:9222/json/version"


def is_cdp_up(timeout: float = 2.0) -> bool:
    """Verifica se la porta di debug CDP (9222) risponde."""
    try:
        req = urllib.request.Request(CDP_URL)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def find_chrome_executable() -> str | None:
    """Trova il percorso dell'eseguibile di Chrome o Chromium."""
    if sys.platform == "win32":
        candidates = [
            Path(os.environ.get("PROGRAMFILES", "C:\\Program Files")) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)")) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        ]
        for c in candidates:
            if c.is_file():
                return str(c)
        return shutil.which("chrome") or shutil.which("google-chrome")
    else:
        for name in ("google-chrome-stable", "google-chrome", "chromium", "chromium-browser"):
            found = shutil.which(name)
            if found:
                return found
    return None


def get_profile_dir() -> Path:
    """Restituisce la directory del profilo Chrome dedicato al debug."""
    env_p = os.environ.get("AGENT_CHROME_PROFILE")
    if env_p:
        return Path(env_p)
    if sys.platform == "win32":
        local_app = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return local_app / "Google" / "Chrome" / "User Data" / "chrome-agent-debug"
    return Path.home() / ".config" / "chrome-agent-debug"


def launch_chrome(extra_args: list[str] | None = None) -> int:
    """Avvia Chrome con i flag di debug CDP configurati."""
    chrome_bin = find_chrome_executable()
    if not chrome_bin:
        print("agent-chrome: Chrome o Chromium non trovato nel sistema.", file=sys.stderr)
        return 127

    profile = get_profile_dir()
    profile.mkdir(parents=True, exist_ok=True)

    cmd = [
        chrome_bin,
        "--remote-debugging-address=127.0.0.1",
        "--remote-debugging-port=9222",
        f"--user-data-dir={profile}",
        "--no-first-run",
    ]
    if extra_args:
        cmd.extend(extra_args)

    try:
        # Avvio in background non bloccante
        if sys.platform == "win32":
            subprocess.Popen(cmd, creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
        else:
            subprocess.Popen(cmd, start_new_session=True)
        return 0
    except OSError as exc:
        print(f"agent-chrome: errore avvio Chrome ({exc})", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    mode = "window"
    passthrough: list[str] = []

    for arg in argv:
        if arg == "--ensure":
            mode = "ensure"
        elif arg == "--heal":
            mode = "heal"
        elif arg in ("-h", "--help"):
            print("Uso: agent-chrome [--ensure|--heal] [args...]\n\nGestisce Chrome con porta CDP locale condivisa.")
            return 0
        else:
            passthrough.append(arg)

    if mode == "ensure":
        if is_cdp_up():
            return 0
        return launch_chrome(passthrough)

    return launch_chrome(passthrough)


if __name__ == "__main__":
    sys.exit(main())
