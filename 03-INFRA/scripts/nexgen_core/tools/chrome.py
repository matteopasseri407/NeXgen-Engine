#!/usr/bin/env python3
"""Management of the shared Chrome browser with a local debug port (CDP 9222).

Contract:
- agent-chrome [args...]: opens the Chrome window or passes the arguments to the existing browser.
- agent-chrome --ensure: exits with 0 if CDP on 127.0.0.1:9222 already responds, otherwise starts Chrome.
- agent-chrome --heal: restarts Chrome if the process is still open without the CDP port,
  or if it has no top-level windows on the user's desktop.
- On Windows, a caller on a desktop the user cannot see starts Chrome through Task Scheduler,
  so the shared browser always comes up on the user's own desktop.
"""
from __future__ import annotations

import base64
import contextlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from nexgen_core.i18n import t
from nexgen_core.paths import resolve_home
from nexgen_core.tools.chrome_windows import (
    USER_DESKTOP,
    WINDOWS_TASK_NAME,
    chrome_windows,
    run_task,
    task_xml,
    windows_desktop,
)

__all__ = [
    "GRACEFUL_SHUTDOWN_SECONDS",
    "TASK_START_SECONDS",
    "USER_DESKTOP",
    "WINDOWS_TASK_NAME",
    "cdp_close_browser",
    "chrome_windows",
    "find_chrome_executable",
    "get_profile_dir",
    "heal_chrome",
    "is_cdp_up",
    "launch_chrome",
    "main",
    "singleton_owner_pid",
    "task_xml",
    "windows_desktop",
]

CDP_URL = "http://127.0.0.1:9222/json/version"

#: How long to wait for Chrome to close on its own before insisting.
GRACEFUL_SHUTDOWN_SECONDS = 10.0

#: How long a Chrome started through the task gets to open the debug port.
TASK_START_SECONDS = 20.0


def is_cdp_up(timeout: float = 2.0) -> bool:
    """Checks whether the CDP debug port (9222) responds."""
    try:
        req = urllib.request.Request(CDP_URL)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001 - browser launch is best-effort
        return False


def cdp_close_browser(timeout: float = 2.0) -> bool:
    """Asks Chrome over CDP to close gracefully (Browser.close)."""
    try:
        req = urllib.request.Request(CDP_URL)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        ws_url = data.get("webSocketDebuggerUrl")
        if not ws_url:
            return False
        parsed = urllib.parse.urlparse(ws_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 9222
        path = parsed.path
        if parsed.query:
            path += f"?{parsed.query}"

        key = base64.b64encode(os.urandom(16)).decode("ascii")
        with socket.create_connection((host, port), timeout=timeout) as s:
            handshake = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n\r\n"
            )
            s.sendall(handshake.encode("ascii"))
            buf = bytearray()
            while b"\r\n\r\n" not in buf:
                chunk = s.recv(4096)
                if not chunk:
                    return False
                buf.extend(chunk)
            if not buf.startswith(b"HTTP/1.1 101"):
                return False

            payload = b'{"id":1,"method":"Browser.close"}'
            mask = os.urandom(4)
            masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            frame = bytes([0x81, 0x80 | len(payload)]) + mask + masked
            s.sendall(frame)
            return True
    except Exception:  # noqa: BLE001 - nexgen: allow reason="graceful close via CDP is best-effort; Chrome may be unresponsive"
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
        canonical = resolve_home() / "ChromeDebugProfile"
        if canonical.is_dir():
            return canonical
        local_app = Path(os.environ.get("LOCALAPPDATA", resolve_home() / "AppData" / "Local"))
        return local_app / "Google" / "Chrome" / "User Data" / "chrome-agent-debug"
    return resolve_home() / ".config" / "chrome-agent-debug"


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


def _run_task(cmd: list[str], desktop: str) -> bool:
    """Registers the on-demand task for `cmd` and runs it; False when Windows refuses."""
    return run_task(cmd, desktop)


def _launch_through_task(cmd: list[str], extra_args: list[str], desktop: str) -> int:
    """Starts Chrome through Task Scheduler, then hands it the caller's arguments.

    Task Scheduler runs an interactive task on the user's desktop whoever asks
    for it, a sandboxed shell included. The arguments (a URL) then reach the
    running browser by Chrome's ordinary single-instance handoff, which opens
    them on the browser's own desktop. If the task cannot run, Chrome is not
    started here: a browser on `desktop` would be invisible to the user.
    """
    print(
        t(
            "agent-chrome: this shell runs on the desktop {desktop}, which the user cannot see. "
            "Starting Chrome on the user's desktop through Task Scheduler.",
            desktop=desktop,
        ),
        file=sys.stderr,
    )
    if not _run_task(cmd, desktop):
        return 1

    deadline = time.monotonic() + TASK_START_SECONDS
    while not is_cdp_up():
        if time.monotonic() >= deadline:
            print(
                t(
                    "agent-chrome: Chrome was started through Task Scheduler but port 9222 "
                    "did not answer within {seconds} seconds.",
                    seconds=int(TASK_START_SECONDS),
                ),
                file=sys.stderr,
            )
            return 1
        time.sleep(0.5)
    if extra_args:
        subprocess.Popen([*cmd, *extra_args], creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
    return 0


def launch_chrome(extra_args: list[str] | None = None) -> int:
    """Launches Chrome with the CDP debug flags configured."""
    chrome_bin = find_chrome_executable()
    if not chrome_bin:
        print(t("agent-chrome: Chrome or Chromium not found on this system."), file=sys.stderr)
        return 127

    profile = get_profile_dir()
    profile.mkdir(parents=True, exist_ok=True)

    base = [
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
        base.append("--class=Google-chrome")
    cmd = [*base, *(extra_args or [])]

    try:
        # Non-blocking background launch
        if sys.platform == "win32":
            # A running browser draws its windows where it lives, so the plain
            # handoff below is right whenever CDP already answers. Only a new
            # browser would inherit this shell's desktop.
            desktop = windows_desktop()
            if desktop is not None and desktop.casefold() != USER_DESKTOP.casefold() and not is_cdp_up():
                return _launch_through_task(base, extra_args or [], desktop)
            subprocess.Popen(cmd, creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
        else:
            subprocess.Popen(cmd, start_new_session=True)
        return 0
    except OSError as exc:
        print(t("agent-chrome: error launching Chrome ({error})", error=exc), file=sys.stderr)
        return 1


def _profile_pattern(profile: str) -> str:
    """The `pkill -f` pattern that names this debug profile and nothing else.

    pkill reads its pattern as a regular expression, and a pattern starting
    with `--` is read as an option unless a `--` precedes it (exit 2, which
    `check=False` hid: the hung browser was never stopped). The path is
    escaped so a dot or a plus in it matches itself.
    """
    escaped = re.sub(r"([.\[\](){}*+?|^$\\])", r"\\\1", profile)
    return f"--user-data-dir={escaped}"


def _terminate_chrome_profile(profile_str: str, cdp_was_up: bool, profile: Path) -> None:
    """Shuts down Chrome processes holding `profile_str`, asking gently before forcing."""
    if sys.platform == "win32":
        closed_gracefully = False
        if cdp_was_up:
            cdp_close_browser()
            for _ in range(int(GRACEFUL_SHUTDOWN_SECONDS * 2)):
                if not is_cdp_up():
                    closed_gracefully = True
                    break
                time.sleep(0.5)
        if not closed_gracefully:
            try:
                from nexgen_core.processes import powershell_literal

                # `.Contains` is an exact substring test; `-like` would read `[` or `*` in the path as wildcards.
                subprocess.run(
                    [
                        "powershell",
                        "-NoProfile",
                        "-Command",
                        "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and "
                        f"$_.CommandLine.Contains({powershell_literal(profile_str)}) }} | "
                        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }",
                    ],
                    capture_output=True,
                    check=False,
                )
            except Exception:  # noqa: BLE001 - nexgen: allow reason="force killing Chrome via PowerShell is best-effort"
                pass
    else:
        # Ask first, insist later: a Chrome killed outright loses the open
        # tabs, and closing them isn't what we were asked to do.
        with contextlib.suppress(OSError):
            subprocess.run(["pkill", "-f", "--", _profile_pattern(profile_str)], capture_output=True, check=False)
        for _ in range(int(GRACEFUL_SHUTDOWN_SECONDS * 2)):
            if singleton_owner_pid(profile) is None:
                break
            time.sleep(0.5)
        else:
            with contextlib.suppress(OSError):
                subprocess.run(
                    ["pkill", "-9", "-f", "--", _profile_pattern(profile_str)],
                    capture_output=True, check=False,
                )


def _heal_chrome_impl(extra_args: list[str] | None = None) -> int:
    cdp_was_up = is_cdp_up()
    if cdp_was_up:
        if sys.platform == "win32":
            windows = chrome_windows(USER_DESKTOP)
            if windows:
                return 0
            print(
                t(
                    "agent-chrome: Chrome is responding on port 9222 but has no top-level windows on {desktop}. Restarting it.",
                    desktop=USER_DESKTOP,
                ),
                file=sys.stderr,
            )
        else:
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

    _terminate_chrome_profile(profile_str, cdp_was_up, profile)
    time.sleep(1.0)
    return launch_chrome(extra_args)


def heal_chrome(extra_args: list[str] | None = None) -> int:
    """Restarts Chrome if the process stayed open without responding on CDP."""
    return _heal_chrome_impl(extra_args)


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
