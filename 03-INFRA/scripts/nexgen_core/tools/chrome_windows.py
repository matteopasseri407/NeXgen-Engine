"""Windows desktop and Task Scheduler management for shared Chrome.

One job: ensure the shared CDP Chrome launches and draws its windows on the
user's interactive desktop (WinSta0\\Default) even when invoked from a private
sandbox desktop.
"""
from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from xml.sax.saxutils import escape

from nexgen_core.files import atomic_write_bytes
from nexgen_core.i18n import t

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


def chrome_windows(desktop: str = USER_DESKTOP) -> list[int]:
    """HWNDs of top-level Chrome windows on `desktop`, or empty list."""
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.OpenDesktopW.restype = wintypes.HANDLE
    user32.OpenDesktopW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    user32.CloseDesktop.argtypes = [wintypes.HANDLE]
    user32.CloseDesktop.restype = wintypes.BOOL
    user32.SetThreadDesktop.argtypes = [wintypes.HANDLE]
    user32.SetThreadDesktop.restype = wintypes.BOOL
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL

    _, _, desk_name = desktop.rpartition("\\")
    matching_windows: list[int] = []

    def enumerate_worker() -> None:
        h_desk = user32.OpenDesktopW(desk_name, 0, False, 0x01FF)
        if not h_desk:
            return
        try:
            if not user32.SetThreadDesktop(h_desk):
                return

            def on_window(hwnd: int, _lparam: int) -> bool:
                if not user32.IsWindowVisible(hwnd):
                    return True
                cbuf = ctypes.create_unicode_buffer(256)
                if user32.GetClassNameW(hwnd, cbuf, 256):
                    cname = cbuf.value
                    if cname == "Chrome_WidgetWin_1" or "chrome" in cname.lower():
                        matching_windows.append(hwnd)
                return True

            user32.EnumWindows(WNDENUMPROC(on_window), 0)
        finally:
            user32.CloseDesktop(h_desk)

    t = threading.Thread(target=enumerate_worker, daemon=True)
    t.start()
    t.join(timeout=5.0)
    return matching_windows


def task_xml(cmd: list[str]) -> str:
    """The Task Scheduler definition that runs `cmd` on the logged-in user's desktop."""
    return _TASK_XML.format(command=escape(cmd[0]), arguments=escape(subprocess.list2cmdline(cmd[1:])))


def run_task(cmd: list[str], desktop: str) -> bool:
    """Registers the on-demand task for `cmd` and runs it; False when Windows refuses.

    Registering on every launch keeps the task's command line in step with the
    current Chrome path and profile.
    """
    from nexgen_core.processes import no_window_kwargs

    handle, xml_path = tempfile.mkstemp(suffix=".xml", prefix="agent-chrome-task-")
    os.close(handle)
    try:
        # UTF-16 with its BOM, which is what Task Scheduler reads, published atomically:
        # a truncated task file makes `schtasks /Create` fail with an XML error nobody can read.
        atomic_write_bytes(Path(xml_path), task_xml(cmd).encode("utf-16"))
        steps = (
            ["schtasks.exe", "/Create", "/TN", WINDOWS_TASK_NAME, "/XML", xml_path, "/F"],
            ["schtasks.exe", "/Run", "/TN", WINDOWS_TASK_NAME],
        )
        for step in steps:
            result = subprocess.run(
                step, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=60, check=False, **no_window_kwargs(),
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


def launch_through_task(cmd: list[str], extra_args: list[str], desktop: str, is_cdp_up_fn) -> int:
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
    if not run_task(cmd, desktop):
        return 1

    deadline = time.monotonic() + TASK_START_SECONDS
    while not is_cdp_up_fn():
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
