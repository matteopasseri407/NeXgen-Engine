"""Launcher generator, from a single description for both platforms.

There's one real command, `nexgen`. Every other name is a legacy alias: we
still generate them because an already-installed machine invokes them by
path, and because the upgrade to the new version is run by the OLD
`nexgen-update`. Breaking the name tree means leaving three machines with
half-working commands.

An alias isn't a copy of the logic: it's `nexgen` with some verbs already in front.
"""
from __future__ import annotations

import stat
import sys
from pathlib import Path

from nexgen_core.paths import resolve_home

#: The real command.
PRIMARY = "nexgen"

#: The legacy names, with the verbs each one prepends. An empty list means
#: "pass the arguments through as-is": `agent-sync doctor -v` becomes
#: `nexgen doctor -v` with no translation.
LEGACY_ALIASES: dict[str, list[str]] = {
    "agent-sync": [],
    "agent-doctor": ["doctor"],
    "vault-push": ["vault", "push"],
    "vault-groom": ["vault", "groom"],
    "vault-map": ["vault", "map"],
    "agent-now": ["tool", "now"],
    "agent-open-folder": ["tool", "open"],
    "agent-chrome": ["tool", "chrome"],
    "firecrawl-local": ["tool", "firecrawl"],
    "nexgen-update": ["update"],
    "skills-sync": ["skill"],
    "agent-skill": ["skill"],
    "council": ["council"],
}

#: Backward compatibility for anyone importing the historical table.
COMMANDS: list[tuple[str, str]] = [(PRIMARY, "nexgen_core/cli/__init__.py")] + [
    (name, "nexgen_core/cli/__init__.py") for name in LEGACY_ALIASES
]

_POSIX_TEMPLATE = """#!/usr/bin/env sh
# NeXgen Engine — auto-generated, do not edit by hand.
# {note}
NEXGEN_ENTRY="{entry}"
if [ -n "${{AGENT_ENGINE_ROOT:-}}" ] && [ -f "$AGENT_ENGINE_ROOT/scripts/nexgen_core/cli/__init__.py" ]; then
    NEXGEN_ENTRY="$AGENT_ENGINE_ROOT/scripts/nexgen_core/cli/__init__.py"
fi
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        exec "$candidate" "$NEXGEN_ENTRY" {prefix}"$@"
    fi
done
echo "NeXgen: Python 3 is not on this system's PATH." >&2
exit 1
"""

_WINDOWS_TEMPLATE = """@echo off
rem NeXgen Engine — auto-generated, do not edit by hand.
rem {note}
setlocal
set "NEXGEN_ENTRY={entry}"
if defined AGENT_ENGINE_ROOT (
    if exist "%AGENT_ENGINE_ROOT%\\scripts\\nexgen_core\\cli\\__init__.py" set "NEXGEN_ENTRY=%AGENT_ENGINE_ROOT%\\scripts\\nexgen_core\\cli\\__init__.py"
)
where py >nul 2>&1
if %ERRORLEVEL% equ 0 (
    py -3 "%NEXGEN_ENTRY%" {prefix}%*
    exit /b %ERRORLEVEL%
)
where python >nul 2>&1
if %ERRORLEVEL% equ 0 (
    python "%NEXGEN_ENTRY%" {prefix}%*
    exit /b %ERRORLEVEL%
)
echo NeXgen: Python 3 is not on this system's PATH. >&2
exit /b 1
"""


def ensure_executable(path: Path) -> None:
    """Sets the executable bit on POSIX systems."""
    if sys.platform != "win32" and path.exists():
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _render(name: str, prefix: list[str], entry: Path, windows: bool) -> str:
    note = (
        f"'{name}' is the historical name for '{PRIMARY} {' '.join(prefix)}'."
        if prefix
        else f"'{name}' is the historical name for '{PRIMARY}'."
    )
    prefix_str = ("".join(f'"{v}" ' for v in prefix)) if prefix else ""
    template = _WINDOWS_TEMPLATE if windows else _POSIX_TEMPLATE
    return template.format(note=note, entry=str(entry), prefix=prefix_str)


def install_shims(
    scripts_dir: Path | None = None,
    bin_dir: Path | None = None,
    home: Path | None = None,
) -> list[str]:
    """Generates the launchers for the current platform and returns what it wrote.

    It's idempotent: regenerating changes nothing if the content already
    matches, which is why the guard cycle can call it every round to
    silently repair a deleted or broken command.
    """
    home_dir = resolve_home(home)
    target_bin = bin_dir or (home_dir / ".local" / "bin")
    target_bin.mkdir(parents=True, exist_ok=True)

    base_scripts = scripts_dir or Path(__file__).resolve().parents[1]
    entry = base_scripts / "nexgen_core" / "cli" / "__init__.py"
    windows = sys.platform == "win32"
    suffix = ".cmd" if windows else ""

    installed: list[str] = []
    for name, prefix in [(PRIMARY, [])] + sorted(LEGACY_ALIASES.items()):
        launcher = target_bin / f"{name}{suffix}"
        content = _render(name, prefix, entry, windows)
        if not (launcher.is_file() and launcher.read_text(encoding="utf-8") == content):
            if launcher.is_symlink():
                launcher.unlink()
            launcher.write_text(content, encoding="utf-8")
        ensure_executable(launcher)
        installed.append(str(launcher))

    return installed
