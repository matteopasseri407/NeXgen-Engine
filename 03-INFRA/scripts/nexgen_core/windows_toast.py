#!/usr/bin/env python3
"""The Windows toast: the engine's identity on the shelf, and the call that shows it.

One job: put a notification on a Windows 10/11 screen from a process that has no
window of its own, and keep the identity Windows insists on before it will show
one at all.

Why this is not inside `notify.py`. What a desktop shows is one decision per
system; on Windows it is also an installation detail. A toast is delivered for an
application Windows already knows, and it knows one by an AppUserModelID plus a
Start-menu entry carrying it. Without that pair the notification is dropped
silently, which is exactly the failure this file exists to end: the old code rang
a tray balloon, and Windows 11 shows those late, small, or never -- especially
when a scheduled task with no console raises them. So the identity is created
here, once, removed with the rest of the engine's autostart entries, and
`notify.py` stays the place that decides which system is speaking.

The click is deliberately harmless: it opens the engine's own report, never an
action, a form or a purchase.

Nothing here raises. An identity that cannot be created, or a notification API
that is not there, answers no, and `notify.py` falls back to the balloon.
"""
from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from nexgen_core.paths import resolve_home
from nexgen_core.processes import no_window_kwargs, powershell_literal

#: The name Windows files this application under. Also the identity a person sees
#: in Settings and in the notification centre, so it is the product name.
AUMID = "NeXgen.Engine"

#: Where Windows keeps the per-application notification settings, which is also
#: where an unpackaged application registers the name it is shown with.
REGISTRY_PATH = rf"Software\Classes\AppUserModelId\{AUMID}"

#: The Start-menu entry that carries `AUMID`. Its presence, with the registration
#: above, is what turns a toast from "silently dropped" into "shown".
SHORTCUT_NAME = "NeXgen Engine.lnk"

#: The display name Windows shows beside the notification.
DISPLAY_NAME = "NeXgen Engine"

#: A notifier that hangs would hold the whole alerting path open.
TIMEOUT_SECONDS = 20

#: `(argv) -> delivered`: the executor, replaceable so a test never spawns PowerShell.
Run = Callable[[list[str]], bool]


def _roaming(home: Path | str | None = None) -> Path:
    """The Roaming folder of the user whose notifications this is.

    An explicit home wins over the environment: a test that asks for a temporary
    home must never be answered with the real Start menu.
    """
    if home is not None:
        return Path(os.fspath(home)) / "AppData" / "Roaming"
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata)
    return resolve_home() / "AppData" / "Roaming"


def shortcut_path(home: Path | str | None = None) -> Path:
    """Where the Start-menu entry lives, in that user's own Programs folder."""
    return _roaming(home) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / SHORTCUT_NAME


def _registry() -> bool:
    """Whether this system has the registry the identity needs (Windows only)."""
    try:
        import winreg  # noqa: F401 - the import is the question
    except ImportError:  # nexgen: allow reason="a system without winreg has no notification identity, and the caller falls back to the balloon"
        return False
    return True


def identity_present(home: Path | str | None = None) -> bool:
    """Is the engine an application Windows will deliver a toast for?

    Two halves, both required: the Start-menu entry and the registration that
    gives the notification a name a person recognises. Checking both keeps a
    half-installed identity from reporting success and then losing every message.
    """
    if not shortcut_path(home).is_file() or not _registry():
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_PATH) as key:
            name, _kind = winreg.QueryValueEx(key, "DisplayName")
    except OSError:  # nexgen: allow reason="an unreadable registration means the identity is missing: answering yes would let the toast be dropped"
        return False
    return bool(str(name).strip())


def _write_registration() -> bool:
    """Registers the name Windows shows beside the notification. Windows only."""
    if not _registry():
        return False
    import winreg

    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, REGISTRY_PATH, 0, winreg.KEY_WRITE
        ) as key:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, DISPLAY_NAME)
            winreg.SetValueEx(key, "ShowInSettings", 0, winreg.REG_DWORD, 1)
    except OSError:  # nexgen: allow reason="a registration this user cannot write is a Windows that will not show the toast, so the answer is no"
        return False
    return True


def _powershell_exe() -> str:
    """The Windows PowerShell this file talks to, by absolute path where possible."""
    root = os.environ.get("SystemRoot") or r"C:\Windows"
    candidate = Path(root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return str(candidate) if candidate.is_file() else "powershell"


def _shortcut_target(home: Path | str | None = None) -> tuple[str, str]:
    """The program and arguments a click on the notification starts.

    The installed launcher where there is one, because that is the same `nexgen`
    the person types; PowerShell with the command where the launcher is not on
    this machine yet. Either way it only prints the engine's own report: a toast
    is a doorbell, and a doorbell that can buy something is a trap.
    """
    base = Path(os.fspath(home)) if home is not None else resolve_home()
    for name in ("nexgen.cmd", "nexgen.exe", "nexgen"):
        candidate = base / ".local" / "bin" / name
        if candidate.is_file():
            return str(candidate), "doctor"
    return _powershell_exe(), "-NoProfile -Command nexgen doctor"


def shortcut_script(home: Path | str | None = None) -> str:
    """The PowerShell that writes the Start-menu entry carrying the identity.

    A `.lnk` is a COM object and the standard library cannot write one, so this is
    the one place the engine needs PowerShell for something other than a launcher.
    """
    target, arguments = _shortcut_target(home)
    link = shortcut_path(home)
    return (
        "$ErrorActionPreference = 'Stop'; "
        f"$dir = {powershell_literal(str(link.parent))}; "
        "New-Item -ItemType Directory -Force -Path $dir > $null; "
        "$shell = New-Object -ComObject WScript.Shell; "
        f"$link = $shell.CreateShortcut({powershell_literal(str(link))}); "
        f"$link.TargetPath = {powershell_literal(target)}; "
        f"$link.Arguments = {powershell_literal(arguments)}; "
        f"$link.Description = {powershell_literal(DISPLAY_NAME)}; "
        "$link.Save()"
    )


def _run(command: list[str]) -> bool:
    """Runs one PowerShell script and says whether it finished cleanly."""
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            check=False,
            timeout=TIMEOUT_SECONDS,
            **no_window_kwargs(),
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):  # nexgen: allow reason="a notifier that fails or hangs is not worth an error: the alert still travels through the other channels and the log"
        return False


def _powershell(script: str, run: Run | None) -> bool:
    """One PowerShell invocation, through the injected executor when there is one."""
    executor = run or _run
    return executor([_powershell_exe(), "-NoProfile", "-NonInteractive", "-Command", script])


def register(home: Path | str | None = None, run: Run | None = None) -> bool:
    """Creates the engine's notification identity. Idempotent and safe to repeat.

    Called from the install lane and, once, from the first alert on a machine that
    was installed before this identity existed. Both halves are written every time
    because a missing half leaves a notification that vanishes without a trace.
    """
    _powershell(shortcut_script(home), run)
    registered = _write_registration()
    return shortcut_path(home).is_file() and registered


def unregister(home: Path | str | None = None) -> bool:
    """Removes what `register` created, so an uninstall leaves no trace behind."""
    removed = True
    try:
        shortcut_path(home).unlink(missing_ok=True)
    except OSError:  # nexgen: allow reason="an uninstall says what it could not delete and keeps going: a leftover entry is reported, not fatal"
        removed = False
    if _registry():
        import winreg

        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, REGISTRY_PATH)
        except FileNotFoundError:  # nexgen: allow reason="already absent is the state an uninstall wants"
            pass
        except OSError:  # nexgen: allow reason="a key that will not be deleted is reported as False for the caller to print, and nothing else can be done"
            removed = False
    return removed


def toast_script(title: str, body: str, aumid: str = AUMID) -> str:
    """The PowerShell that shows one toast through the notification API.

    Two details are the whole trick: the notification type comes from WinRT, and
    the notifier is created *for the registered identity* rather than for the
    process, which is what lets a console-less scheduled task speak at all. The
    text goes in as a PowerShell literal, never as markup, because an alert can
    carry a file name or an error message chosen from outside. `long` keeps it on
    screen for the twenty-five seconds an alert about a broken machine deserves.
    """
    return (
        "$ErrorActionPreference = 'Stop'; "
        "try { "
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
        "ContentType = WindowsRuntime] > $null; "
        "$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
        "[Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
        "$template.DocumentElement.SetAttribute('duration', 'long'); "
        "$texts = $template.GetElementsByTagName('text'); "
        f"$texts.Item(0).AppendChild($template.CreateTextNode({powershell_literal(title)})) > $null; "
        f"$texts.Item(1).AppendChild($template.CreateTextNode({powershell_literal(body)})) > $null; "
        "$toast = [Windows.UI.Notifications.ToastNotification]::new($template); "
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
        f"{powershell_literal(aumid)}).Show($toast); "
        "exit 0 "
        "} catch { exit 1 }"
    )


def show(title: str, body: str, home: Path | str | None = None, run: Run | None = None) -> bool:
    """Shows one toast, and says whether the notification API accepted it.

    The identity is created on the spot when it is missing: a machine installed
    before this file existed would otherwise keep receiving nothing, because
    Windows drops a toast from an application it does not know. When even that
    fails, the answer is no and the caller takes the balloon.
    """
    if not sys.platform.startswith("win"):
        return False
    if not identity_present(home) and not register(home, run):
        return False
    return _powershell(toast_script(title, body), run)
