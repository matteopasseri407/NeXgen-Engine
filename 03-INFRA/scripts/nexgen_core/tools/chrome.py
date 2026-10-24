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
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape

from nexgen_core.i18n import t
from nexgen_core.paths import resolve_home

CDP_URL = "http://127.0.0.1:9222/json/version"


def is_cdp_up(timeout: float = 2.0) -> bool:
    """Checks whether the CDP debug port (9222) responds."""
    try:
        req = urllib.request.Request(CDP_URL)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001 - browser launch is best-effort
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


#: The desktop the person at the keyboard actually sees on Windows.
USER_DESKTOP = "WinSta0\\Default"

#: The on-demand Task Scheduler entry that starts Chrome on the user's desktop.
WINDOWS_TASK_NAME = "NeXgen Agent Chrome"

#: How long a Chrome started through the task gets to open the debug port.
TASK_START_SECONDS = 20.0

_UOI_NAME = 2

# No time limit (the default 72 hours kills the browser), allowed on battery,
# normal priority (a task otherwise runs below normal) and a single instance.
# No trigger: the task only runs when the launcher asks for it.
_TASK_XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Starts the shared CDP Chrome on the user's own desktop (agent-chrome).</Description>
  </RegistrationInfo>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>4</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{command}</Command>
      <Arguments>{arguments}</Arguments>
    </Exec>
  </Actions>
</Task>
"""


def windows_desktop() -> str | None:
    """The `station\\desktop` this process draws on, or None when Windows won't say.

    A child process inherits the desktop of whoever starts it. An agent CLI
    that sandboxes its shell on a private desktop (Antigravity's `exebox-...`)
    therefore starts a Chrome nobody can see, while CDP answers as if all
    were well.
    """
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32")
    user32.GetProcessWindowStation.restype = wintypes.HANDLE
    user32.GetThreadDesktop.restype = wintypes.HANDLE
    user32.GetThreadDesktop.argtypes = [wintypes.DWORD]
    user32.GetUserObjectInformationW.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
    ]

    def object_name(handle: int | None) -> str | None:
        buf = ctypes.create_unicode_buffer(256)
        needed = wintypes.DWORD()
        if not handle or not user32.GetUserObjectInformationW(
            handle, _UOI_NAME, buf, ctypes.sizeof(buf), ctypes.byref(needed)
        ):
            return None
        return buf.value

    station = object_name(user32.GetProcessWindowStation())
    desktop = object_name(user32.GetThreadDesktop(kernel32.GetCurrentThreadId()))
    if station is None or desktop is None:
        return None
    return f"{station}\\{desktop}"


def task_xml(cmd: list[str]) -> str:
    """The Task Scheduler definition that runs `cmd` on the logged-in user's desktop."""
    return _TASK_XML.format(command=escape(cmd[0]), arguments=escape(subprocess.list2cmdline(cmd[1:])))


def _run_task(cmd: list[str], desktop: str) -> bool:
    """Registers the on-demand task for `cmd` and runs it; False when Windows refuses.

    Registering on every launch keeps the task's command line in step with the
    current Chrome path and profile.
    """
    from nexgen_core.processes import no_window_kwargs

    handle, xml_path = tempfile.mkstemp(suffix=".xml", prefix="agent-chrome-task-")
    os.close(handle)
    try:
        Path(xml_path).write_text(task_xml(cmd), encoding="utf-16")
        steps = (
            ["schtasks.exe", "/Create", "/TN", WINDOWS_TASK_NAME, "/XML", xml_path, "/F"],
            ["schtasks.exe", "/Run", "/TN", WINDOWS_TASK_NAME],
        )
        for step in steps:
            result = subprocess.run(
                step, capture_output=True, text=True, timeout=60, check=False, **no_window_kwargs()
            )
            if result.returncode != 0:
                print(
                    t(
                        "agent-chrome: Task Scheduler could not start Chrome ({error}). "
                        "Not starting it from here: on {desktop} it would be invisible.",
                        error=(result.stderr or result.stdout).strip(),
                        desktop=desktop,
                    ),
                    file=sys.stderr,
                )
                return False
    finally:
        with contextlib.suppress(OSError):
            os.unlink(xml_path)
    return True


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


def _heal_chrome_impl(extra_args: list[str] | None = None) -> int:
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
        except Exception:  # noqa: BLE001 - browser launch is best-effort
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
