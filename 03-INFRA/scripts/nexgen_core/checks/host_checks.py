"""Checks of what makes the engine run at all: the timers that start the guard, and the commands that start the engine."""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

from nexgen_core.i18n import t
from nexgen_core.report import CheckOutcome, Severity

TIMERS = ("agent-sync.timer", "agent-heartbeat.timer")
_LAUNCHER_MARKER = "NeXgen Engine"
#: The path a launcher falls back to: `NEXGEN_ENTRY="..."` in the POSIX one, `set "NEXGEN_ENTRY=..."` in the Windows one.
_ENTRY_LINE = re.compile(r'^(?:set ")?NEXGEN_ENTRY=?"?([^"\r\n]+)"', re.MULTILINE)


def _systemctl(args: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return proc.returncode, (proc.stdout or "").strip()


def check_timers_armed() -> CheckOutcome:
    """Are the timers that start the guard and the heartbeat enabled and counting down?

    Everything else the engine promises (drift repaired, a stale guard noticed, an update applied)
    depends on these two. If they are written but not enabled, or enabled but stopped, nothing fails
    loudly: the machine simply stops being tended, and the heartbeat that would say so is one of the
    two timers that are off.
    """
    from nexgen_core.scheduler import host_mutations_disabled

    if not sys.platform.startswith("linux"):
        return CheckOutcome(id="host.timers", severity=Severity.UNDETERMINED,
                            message=t("Scheduled-task state is checked on Linux/systemd only; not checked here."))
    if host_mutations_disabled() or shutil.which("systemctl") is None:
        return CheckOutcome(id="host.timers", severity=Severity.UNDETERMINED,
                            message=t("systemd is not available here (or host changes are disabled): the timers were not checked."))
    problems: list[str] = []
    for unit in TIMERS:
        enabled_rc, enabled = _systemctl(["is-enabled", unit])
        active_rc, active = _systemctl(["is-active", unit])
        if enabled_rc != 0 or active_rc != 0:
            problems.append(f"{unit} ({enabled or 'not found'}/{active or 'not found'})")
    if problems:
        return CheckOutcome(
            id="host.timers",
            severity=Severity.BROKEN,
            message=t("The guard's timers are not running, so nothing tends this machine: {units}", units=", ".join(problems)),
            action=t("Run 'nexgen guard' once to write and enable them. On a machine with no login session, also: loginctl enable-linger $USER."),
        )
    return CheckOutcome(id="host.timers", severity=Severity.OK, message=t("The guard and heartbeat timers are enabled and running"))


def _engine_launchers(bin_dir: Path) -> list[Path]:
    if not bin_dir.is_dir():
        return []
    found = []
    for path in sorted(bin_dir.iterdir()):
        if not path.is_file() or path.suffix not in ("", ".cmd"):
            continue
        try:
            head = path.read_text(encoding="utf-8", errors="replace")[:400]
        except OSError:
            continue
        if _LAUNCHER_MARKER in head and "auto-generated" in head:
            found.append(path)
    return found


def check_launchers(home: Path) -> CheckOutcome:
    """Does every command the engine generated point at an engine that is still there?

    A launcher keeps the path of the checkout it was written for. If the checkout moved or was deleted the
    command fails with Python's "can't open file", and the guard that would rewrite it is started by the
    same commands.
    """
    bin_dir = home / ".local" / "bin"
    launchers = _engine_launchers(bin_dir)
    if not launchers:
        return CheckOutcome(id="host.launchers", severity=Severity.UNDETERMINED,
                            message=t("No engine launchers found in {dir}; not checked.", dir=bin_dir))
    broken: list[str] = []
    for launcher in launchers:
        match = _ENTRY_LINE.search(launcher.read_text(encoding="utf-8", errors="replace"))
        if match and not Path(match.group(1)).is_file():
            broken.append(f"{launcher.name} -> {match.group(1)}")
    if broken:
        return CheckOutcome(
            id="host.launchers",
            severity=Severity.BROKEN,
            message=t("Some engine commands point at an engine that is no longer there: {commands}", commands="; ".join(broken[:5])),
            action=t("Run 'nexgen init' from the engine you want to use: it rewrites every launcher."),
        )
    return CheckOutcome(id="host.launchers", severity=Severity.OK,
                        message=t("Every engine command points at an engine that exists ({count} checked)", count=len(launchers)))
