#!/usr/bin/env python3
"""Management of the shared Chrome browser with a local debug port (CDP 9222).

Contract:
- agent-chrome [args...]: opens the Chrome window or passes the arguments to the existing browser.
- agent-chrome --ensure: exits with 0 if CDP on 127.0.0.1:9222 already responds, otherwise starts Chrome.
- agent-chrome --heal: restarts Chrome if the process is still open without the CDP port.
"""
from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from nexgen_core.i18n import t
from nexgen_core.paths import resolve_home

CDP_URL = "http://127.0.0.1:9222/json/version"


def is_cdp_up(timeout: float = 2.0) -> bool:
    """Checks whether the CDP debug port (9222) responds."""
    try:
        req = urllib.request.Request(CDP_URL)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def find_chrome_executable() -> str | None:
    """Finds the path to the Chrome or Chromium executable."""
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
    """Returns the Chrome profile directory dedicated to debugging."""
    env_p = os.environ.get("AGENT_CHROME_PROFILE")
    if env_p:
        return Path(env_p)
    if sys.platform == "win32":
        local_app = Path(os.environ.get("LOCALAPPDATA", resolve_home() / "AppData" / "Local"))
        return local_app / "Google" / "Chrome" / "User Data" / "chrome-agent-debug"
    return resolve_home() / ".config" / "chrome-agent-debug"


#: How long to wait for Chrome to close on its own before insisting.
GRACEFUL_SHUTDOWN_SECONDS = 10.0


def singleton_owner_pid(profile: Path) -> int | None:
    """Who's holding the debug profile, if anyone is.

    Chrome writes a `SingletonLock` pointing at `host-pid`. This is needed
    to distinguish "Chrome isn't there" from "Chrome is there but lost the
    debug port": two situations with different remedies that get confused
    without this.
    """
    lock = profile / "SingletonLock"
    try:
        target = os.readlink(lock)
    except OSError:
        return None
    _, _, pid = target.rpartition("-")
    try:
        return int(pid)
    except ValueError:
        return None


def launch_chrome(extra_args: list[str] | None = None) -> int:
    """Launches Chrome with the CDP debug flags configured."""
    chrome_bin = find_chrome_executable()
    if not chrome_bin:
        print(t("agent-chrome: Chrome or Chromium not found on this system."), file=sys.stderr)
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
    if sys.platform not in ("win32", "darwin"):
        # Without this, windows opened from here end up grouped under a
        # generic class and the window manager can't tell them apart from
        # apps installed as a shortcut.
        cmd.append("--class=Google-chrome")
    if extra_args:
        cmd.extend(extra_args)

    try:
        # Non-blocking background launch
        if sys.platform == "win32":
            subprocess.Popen(cmd, creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
        else:
            subprocess.Popen(cmd, start_new_session=True)
        return 0
    except OSError as exc:
        print(t("agent-chrome: error launching Chrome ({error})", error=exc), file=sys.stderr)
        return 1


def heal_chrome(extra_args: list[str] | None = None) -> int:
    """Restarts Chrome if the process stayed open without responding on CDP."""
    if is_cdp_up():
        return 0

    profile = get_profile_dir()
    profile_str = str(profile)

    owner = singleton_owner_pid(profile)
    if owner is not None:
        print(
            t(
                "agent-chrome: Chrome is holding the debug profile (process {pid}) "
                "but isn't responding on port 9222. Restarting it.",
                pid=owner,
            ),
            file=sys.stderr,
        )

    # Terminate any Chrome processes associated with the debug profile
    if sys.platform == "win32":
        try:
            subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    f"Get-CimInstance Win32_Process | Where-Object {{ $_.CommandLine -like '*{profile_str}*' }} | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}",
                ],
                capture_output=True,
                check=False,
            )
        except Exception:
            pass
    else:
        # Ask first, insist later: a Chrome killed outright loses the open
        # tabs, and closing them isn't what we were asked to do.
        with contextlib.suppress(OSError):
            subprocess.run(["pkill", "-f", f"--user-data-dir={profile_str}"], capture_output=True, check=False)
        for _ in range(int(GRACEFUL_SHUTDOWN_SECONDS * 2)):
            if singleton_owner_pid(profile) is None:
                break
            time.sleep(0.5)
        else:
            with contextlib.suppress(OSError):
                subprocess.run(
                    ["pkill", "-9", "-f", f"--user-data-dir={profile_str}"],
                    capture_output=True, check=False,
                )

    time.sleep(1.0)
    return launch_chrome(extra_args)


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
            print(t("Usage: agent-chrome [--ensure|--heal] [args...]\n\nManages Chrome with a shared local CDP port."))
            return 0
        else:
            passthrough.append(arg)

    if mode == "ensure":
        if is_cdp_up():
            return 0
        return launch_chrome(passthrough)
    elif mode == "heal":
        return heal_chrome(passthrough)

    return launch_chrome(passthrough)


if __name__ == "__main__":
    sys.exit(main())
