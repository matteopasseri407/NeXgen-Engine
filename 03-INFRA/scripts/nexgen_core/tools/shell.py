"""Retired interactive NeXgen Engine Shell / REPL.

The interactive shell has been retired in favor of OpenClaw TUI (`openclaw tui`),
Web Dashboard, and Control UI.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from nexgen_core.i18n import t
from nexgen_core.processes import split_command_line  # noqa: F401

RETIREMENT_NOTICE = "The interactive shell has been retired: use 'openclaw tui' or the OpenClaw Dashboard instead."

MENU_ACTIONS = [
    ("0", "openclaw", "Use 'openclaw tui' or OpenClaw Dashboard"),
]


def render_menu() -> str:
    """Returns the retirement notice pointing to OpenClaw."""
    return t(RETIREMENT_NOTICE)


class NeXgenShell:
    """Stub for retired interactive shell for backward compatibility."""

    COMMANDS = ["doctor", "sync", "info", "exit", "quit"]
    MODULE_SUBCMDS: list[str] = []
    SECRETS_SUBCMDS: list[str] = []
    VAULT_SUBCMDS: list[str] = []

    def __init__(self, vault_data: Path | None = None) -> None:
        self.vault_data = vault_data

    def cmdloop(self, intro: Any = None) -> None:
        print(t(RETIREMENT_NOTICE))

    def default(self, line: str) -> bool:
        print(t(RETIREMENT_NOTICE))
        return False

    def do_info(self, arg: str = "") -> None:
        print(t(RETIREMENT_NOTICE))

    def do_doctor(self, arg: str = "") -> None:
        print(t(RETIREMENT_NOTICE))

    def do_sync(self, arg: str = "") -> None:
        print(t(RETIREMENT_NOTICE))

    def do_exit(self, arg: str = "") -> bool:
        return True

    def do_quit(self, arg: str = "") -> bool:
        return True

    def do_help(self, arg: str = "") -> None:
        print(t(RETIREMENT_NOTICE))


def _run_shell_impl(vault_data: Path | None = None) -> int:
    print(t(RETIREMENT_NOTICE))
    return 0


def run_shell(vault_data: Path | None = None) -> int:
    """Prints guidance pointing to OpenClaw TUI and dashboard."""
    return _run_shell_impl(vault_data=vault_data)
