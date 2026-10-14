#!/usr/bin/env python3
"""What is actually supported, stated once.

The README used to describe platform support in prose, in two languages,
and the prose had drifted from the code: `install.sh` handled Linux, macOS
and Windows, the README declared two of them, and macOS was in the code
without ever being declared anywhere. It also said the tools were verified
"on Fedora", which stopped being true when the maintainer's machine moved to
Ubuntu — a sentence that tells the reader about the author's laptop rather
than about the product.

So the status lives here, as data, and the README section is generated from
it. A test fails when the two disagree, which is the only thing that keeps a
support claim honest over time: someone has to change the data to change the
promise.

The wording of each status is deliberately about evidence, not confidence.
"Released" says what was run, not how sure anybody feels.
"""
from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from nexgen_core.paths import resolve_home


@dataclass(frozen=True)
class Support:
    """One row: what it is, how far it got, and on what evidence."""

    name: str
    status: str
    evidence: str


#: Operating systems. Anything the installer can start on belongs here,
#: declared — including the ones that are only partly proven. A platform
#: present in the code and absent from this table is the bug this file
#: exists to prevent.
OPERATING_SYSTEMS: tuple[Support, ...] = (
    Support(
        "Linux",
        "released",
        "the platform this is developed and used on daily; the full cycle (install, "
        "alignment, doctor, grooming, council, update) runs here and in CI",
    ),
    Support(
        "Windows",
        "released",
        "verified on real hardware and in CI; full native Python execution, native command shims, and complete CLI alignment",
    ),
    Support(
        "macOS",
        "untested",
        "shares the POSIX paths with Linux and should work, but nobody has run it "
        "end to end; treat a failure here as expected, and reporting it as useful",
    ),
)

#: The assistants the engine configures. "Half supported" is worse than
#: unsupported, so a runtime that is not complete says exactly where it stops.
RUNTIMES: tuple[Support, ...] = (
    Support("Claude Code", "complete", "instructions, MCP connectors, skills, guardrails"),
    Support("Codex", "complete", "instructions, MCP connectors, skills"),
    Support(
        "OpenCode",
        "complete",
        "scope-file instructions, MCP connectors, native plugins/permissions, skills, and a Council seat",
    ),
    Support(
        "Antigravity",
        "complete",
        "instructions, MCP connectors, skills, and a Council seat; the Council "
        "adapter uses agy --print --model ... --disable-slash-commands --new-project "
        "--sandbox with the prompt on stdin; vendor isolation limits are documented in docs/council.md",
    ),
)

@dataclass(frozen=True)
class DesktopApp:
    """A desktop app that shares its config with a CLI the engine already configures.

    ChatGPT desktop bundles Codex and reads the same ~/.codex as the CLI;
    Antigravity IDE reads the same ~/.gemini as `agy`. So an app on its own
    must still get the targets it shares, and the locations below are only
    used to decide that the app is on this machine.

    `bundled` names the CLI binary shipped inside the app, relative to the
    app root, when there is one. The macOS and Windows layouts are not
    verified on a real install; only the Linux one is.
    """

    name: str
    targets: tuple[str, ...]
    locations: Mapping[str, tuple[str, ...]]
    bundled: tuple[str, ...] = ()


#: Where each app lives per operating-system family ("linux", "darwin",
#: "win32"). `%LOCALAPPDATA%` is expanded at lookup time. The Windows Store
#: package is not listed: its folder under WindowsApps is versioned and not
#: readable without elevated rights, so it cannot be detected honestly.
DESKTOP_APPS: tuple[DesktopApp, ...] = (
    DesktopApp(
        "ChatGPT",
        ("codex",),
        {
            "linux": ("/usr/lib/chatgpt", "/usr/bin/chatgpt"),
            "darwin": ("/Applications/ChatGPT.app",),
            "win32": ("%LOCALAPPDATA%/Programs/ChatGPT",),
        },
        bundled=("resources/codex", "resources/codex.exe", "Contents/Resources/codex"),
    ),
    DesktopApp(
        "Antigravity",
        ("antigravity",),
        {
            "linux": ("/opt/antigravity-ide", "/usr/share/antigravity"),
            "darwin": ("/Applications/Antigravity.app",),
            "win32": ("%LOCALAPPDATA%/Programs/Antigravity",),
        },
    ),
)


def _family(platform: str) -> str:
    if platform.startswith("win"):
        return "win32"
    if platform == "darwin":
        return "darwin"
    return "linux"


def _candidate_roots(app: DesktopApp, family: str, env: Mapping[str, str]) -> list[Path]:
    roots: list[Path] = []
    local_app_data = env.get("LOCALAPPDATA") or str(resolve_home() / "AppData" / "Local")
    for raw in app.locations.get(family, ()):
        roots.append(Path(raw.replace("%LOCALAPPDATA%", local_app_data)))
    return roots


def existing_roots(app: DesktopApp, platform: str | None = None, env: Mapping[str, str] | None = None) -> list[Path]:
    """The install locations of one app that are present on this machine."""
    family = _family(sys.platform if platform is None else platform)
    environment = os.environ if env is None else env
    return [root for root in _candidate_roots(app, family, environment) if root.exists()]


def installed_desktop_apps(
    platform: str | None = None,
    env: Mapping[str, str] | None = None,
    exists: Callable[[Path], bool] = Path.exists,
) -> list[DesktopApp]:
    """The desktop apps present on this machine, in declaration order.

    `platform`, `env` and `exists` are injectable so every operating system
    can be exercised from one test run. A missing or unreadable location is
    simply "not installed": detection never raises.
    """
    family = _family(sys.platform if platform is None else platform)
    environment = os.environ if env is None else env
    found: list[DesktopApp] = []
    for app in DESKTOP_APPS:
        try:
            if any(exists(root) for root in _candidate_roots(app, family, environment)):
                found.append(app)
        except OSError:
            continue
    return found


START = "<!-- platform-status:start -->"
END = "<!-- platform-status:end -->"


def render_markdown() -> str:
    """The section as it must appear, between its two markers."""
    lines = [START, "", "| System | Status | On what evidence |", "|---|---|---|"]
    lines += [f"| {s.name} | **{s.status}** | {s.evidence} |" for s in OPERATING_SYSTEMS]
    lines += ["", "| Assistant | Status | What is covered |", "|---|---|---|"]
    lines += [f"| {s.name} | **{s.status}** | {s.evidence} |" for s in RUNTIMES]
    lines += ["", END]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Prints the table. Used by the docs check and by anyone who asks."""
    print(render_markdown())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
