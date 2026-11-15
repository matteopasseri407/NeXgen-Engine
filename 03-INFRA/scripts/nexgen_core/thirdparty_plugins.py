"""The plugins the harnesses own: what is installed, and whose command moves it.

One job: ask each harness that can install plugins for its own list, in the
machine-readable form it already prints, and answer one row per plugin naming the
command that updates it. Nothing here installs, updates, enables or disables
anything: a plugin's lifecycle belongs to the harness that loaded it, and the
engine's job is to say where it stands and what to type.

The three readers were checked on this machine (2026-11-15) before being written:

- Claude Code: `claude plugin list --json` (a list), `claude plugin update <plugin>`.
- Codex: `codex plugin list --json` (`{"installed": [...]}`),
  `codex plugin marketplace upgrade` (no per-plugin verb: the marketplaces move).
- Antigravity: `agy plugin list --json` (a list), `agy plugin install <plugin>`
  (no update verb: installing again is the update).

A harness that is not installed answers nothing and costs nothing; one that
answers in a shape this module does not recognise is refused whole, so a
release that renames a field costs that harness's rows and never invents them.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from collections.abc import Callable, Sequence

from nexgen_core.processes import no_window_kwargs, windows_command_argv

logger = logging.getLogger(__name__)

#: One listing waits at most this long. These CLIs are already installed and
#: answer from their own cache; the bound exists so a stuck one cannot hold the
#: command a person typed.
LIST_TIMEOUT_SECONDS = 20

#: What each harness reports with, and the verb that moves a plugin it owns.
HARNESSES: dict[str, tuple[Sequence[str], str]] = {
    "claude": (("plugin", "list", "--json"), "plugin update"),
    "codex": (("plugin", "list", "--json"), "plugin marketplace upgrade"),
    "antigravity": (("plugin", "list", "--json"), "plugin install"),
}


@dataclass(frozen=True)
class PluginRow:
    """One installed plugin, the harness that owns it, and the command that moves it."""

    name: str
    harness: str
    version: str
    enabled: bool
    command: str


def _run(exe: str, argv: Sequence[str]) -> subprocess.CompletedProcess[str] | None:
    """One listing, windowless and bounded; None when the CLI cannot be started."""
    try:
        return subprocess.run(
            windows_command_argv([exe, *argv]), stdin=subprocess.DEVNULL,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False, timeout=LIST_TIMEOUT_SECONDS, **no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):  # nexgen: allow reason="a CLI that will not start has no listing: the contract answers None and the caller prints that harness as unavailable"
        return None


def _entries(harness: str, data) -> list[dict] | None:
    """The plugin entries of one harness' answer, or None when the shape is unknown."""
    if harness == "codex":
        rows = data.get("installed") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            return None
        return [row for row in rows if isinstance(row, dict)]
    if not isinstance(data, list):
        return None
    return [row for row in data if isinstance(row, dict)]


def _row_of(harness: str, entry: dict) -> PluginRow | None:
    """One listing entry as a row; None when it names no plugin."""
    name = str(entry.get("id") or entry.get("pluginId") or entry.get("name") or "").split("@")[0].strip()
    if not name:
        return None
    verb = HARNESSES[harness][1]
    needs_name = verb != "plugin marketplace upgrade"
    return PluginRow(
        name=name,
        harness=harness,
        version=str(entry.get("version") or ""),
        enabled=bool(entry.get("enabled", True)),
        command=f"{_exe(harness)} {verb}{' ' + name if needs_name else ''}",
    )


def _exe(harness: str) -> str:
    """The command a person types for this harness (Antigravity's CLI is `agy`)."""
    return "agy" if harness == "antigravity" else harness


def rows(which: Callable[[str], "str | None"] | None = None) -> tuple[list[PluginRow], list[str]]:
    """Every plugin the harnesses report, and one line per harness that could not be asked.

    `which` and `run` are looked up at call time and replaceable, so a test states
    the machine it describes instead of inheriting this one.
    """
    lookup = which or shutil.which
    found: list[PluginRow] = []
    problems: list[str] = []
    for harness, (argv, _verb) in HARNESSES.items():
        exe = _exe(harness)
        if not lookup(exe):
            continue
        result = _run(exe, argv)
        if result is None or result.returncode != 0 or not result.stdout.strip():
            problems.append(f"{exe} {' '.join(argv)} did not answer")
            continue
        try:
            data = json.loads(result.stdout)
        except ValueError:
            problems.append(f"{exe} {' '.join(argv)} answered something that is not JSON")
            continue
        entries = _entries(harness, data)
        if entries is None:
            problems.append(f"{exe} {' '.join(argv)} answered a shape this engine does not know")
            continue
        found.extend(row for row in (_row_of(harness, entry) for entry in entries) if row)
    return found, problems
