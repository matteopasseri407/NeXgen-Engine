#!/usr/bin/env python3
"""Delivering a message to the screen in front of the person.

This is not a second megaphone. The megaphone still owns what is worth saying,
how it is worded and how often it may be repeated; this module only knows how
a desktop shows a notification, which is a different thing on every system and
belongs in one place rather than inside three branches of an alerting rule.

It matters more than it looks. Someone who has not configured a messaging bot
receives nothing at all today: the alert is composed, the debounce is honoured,
and then it goes nowhere. For a person who installed this to keep their notes
in order, the desktop is the only channel that reaches them.

Every function here fails quietly. A notification that cannot be shown is not
worth an error: the message still travels through the other transports, and
the log still has it.
"""
from __future__ import annotations

import shutil
import subprocess
import sys

#: A notifier that hangs would hold the whole alerting path open.
NOTIFY_TIMEOUT_SECONDS = 10


def _run(command: list[str]) -> bool:
    try:
        result = subprocess.run(
            command, capture_output=True, check=False, timeout=NOTIFY_TIMEOUT_SECONDS
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _linux(title: str, body: str) -> bool:
    if shutil.which("notify-send") is None:
        return False
    # `critical` keeps the notification on screen until it is dismissed. The
    # megaphone only wakes for things a person has to act on, so a toast that
    # disappears while they are away defeats the point of sending it.
    return _run(["notify-send", "--urgency=critical", "--app-name=NeXgen", title, body])


def _macos(title: str, body: str) -> bool:
    if shutil.which("osascript") is None:
        return False
    escaped_title = title.replace('"', '\\"')
    escaped_body = body.replace('"', '\\"')
    script = f'display notification "{escaped_body}" with title "{escaped_title}"'
    return _run(["osascript", "-e", script])


def _windows(title: str, body: str) -> bool:
    if shutil.which("powershell") is None:
        return False
    # BurntToast is not assumed: a balloon through the shell's own tray icon
    # needs nothing installed, which is the difference between a notification
    # that arrives on a fresh machine and one that arrives on a prepared one.
    safe_title = title.replace("'", "''")
    safe_body = body.replace("'", "''")
    script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$n = New-Object System.Windows.Forms.NotifyIcon; "
        "$n.Icon = [System.Drawing.SystemIcons]::Warning; "
        "$n.Visible = $true; "
        f"$n.ShowBalloonTip(10000, '{safe_title}', '{safe_body}', "
        "[System.Windows.Forms.ToolTipIcon]::Warning); "
        "Start-Sleep -Seconds 10; $n.Dispose()"
    )
    return _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script])


def desktop_is_available() -> bool:
    """Is there a desktop here at all?

    A server over SSH has no screen to show anything on, and trying is a
    guaranteed failure that only slows the alert down.
    """
    if sys.platform == "win32":
        return shutil.which("powershell") is not None
    if sys.platform == "darwin":
        return shutil.which("osascript") is not None
    import os

    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False
    return shutil.which("notify-send") is not None


def send_desktop(title: str, body: str) -> bool:
    """Shows the notification, and says whether anyone could have seen it."""
    if not title.strip() and not body.strip():
        return False
    if sys.platform == "win32":
        return _windows(title, body)
    if sys.platform == "darwin":
        return _macos(title, body)
    return _linux(title, body)
