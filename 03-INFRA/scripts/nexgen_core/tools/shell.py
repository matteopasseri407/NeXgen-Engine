"""Interactive NeXgen Engine Shell / REPL.

Allows human operators to inspect system health, manage modules, run sync,
check secrets, and update the engine from a standalone interactive session
with a visual numbered menu and direct command execution.
"""
from __future__ import annotations

import cmd
import os
import shlex
import sys
from pathlib import Path
from typing import Any

from nexgen_core import __version__
from nexgen_core.i18n import t
from nexgen_core.paths import resolve_home, resolve_vault_data
from nexgen_core.tools.info import (
    C_BOLD,
    C_DARK_SLATE,
    C_DIM,
    C_EMERALD,
    C_GREEN,
    C_RESET,
    C_SLATE,
    C_WHITE,
    C_YELLOW,
    render_info,
)


def _setup_readline() -> None:
    try:
        import readline
        history_file = resolve_home() / ".config" / "nexgen" / "shell_history"
        history_file.parent.mkdir(parents=True, exist_ok=True)
        if history_file.is_file():
            try:
                readline.read_history_file(str(history_file))
            except OSError:
                pass

        import atexit

        def _save_hist() -> None:
            try:
                readline.write_history_file(str(history_file))
            except OSError:
                pass

        atexit.register(_save_hist)
    except (ImportError, AttributeError):
        pass


MENU_ACTIONS = [
    ("1", "doctor", "Run health diagnostics & automatic remedies"),
    ("2", "sync", "Realign MCP & skill configs across all 4 runtimes"),
    ("3", "modules", "Inspect and configure modules (local/remote/absent)"),
    ("4", "secrets", "Manage age secrets store & materialize secrets.env"),
    ("5", "vault", "Inspect KnowledgeVault link hygiene, hubs and orphans"),
    ("6", "update", "Check and update NeXgen Engine release"),
    ("7", "info", "Redraw architecture dashboard & component status"),
    ("0", "exit", "Exit interactive shell"),
]


def render_menu() -> str:
    lines = []
    lines.append(f"  {C_EMERALD}{C_BOLD}SELECTABLE ACTIONS (Type number or command name):{C_RESET}")
    lines.append(f"  {C_DARK_SLATE}{'─' * 68}{C_RESET}")
    for num, name, desc in MENU_ACTIONS:
        lines.append(
            f"  {C_EMERALD}[{num}]{C_RESET} {C_BOLD}{C_WHITE}{name:<11}{C_RESET} {C_SLATE}{desc}{C_RESET}"
        )
    lines.append(f"  {C_DARK_SLATE}{'─' * 68}{C_RESET}")
    return "\n".join(lines)


class NeXgenShell(cmd.Cmd):
    """Interactive command-line interface for human operators."""

    intro = ""
    prompt = f"{C_EMERALD}{C_BOLD}nexgen > {C_RESET}"

    COMMANDS = [
        "doctor", "sync", "modules", "secrets", "vault",
        "update", "info", "status", "clear", "help", "exit", "quit",
    ]

    MODULE_SUBCMDS = ["list", "set"]
    SECRETS_SUBCMDS = ["list", "materialize", "doctor", "get", "set", "set-oauth", "enroll"]
    VAULT_SUBCMDS = ["map", "push"]

    def __init__(self, vault_data: Path | None = None) -> None:
        super().__init__()
        self.vault_data = vault_data
        _setup_readline()

    def emptyline(self) -> bool:
        return False

    def default(self, line: str) -> bool:
        """Handles numeric shortcuts or forwards directly to the nexgen CLI engine."""
        raw = line.strip()
        if not raw:
            return False

        # Number shortcuts
        if raw == "1":
            self.do_doctor("")
            return False
        elif raw.startswith("1 "):
            self.do_doctor(raw[2:].strip())
            return False
        elif raw == "2":
            self.do_sync("")
            return False
        elif raw.startswith("2 "):
            self.do_sync(raw[2:].strip())
            return False
        elif raw == "3" or raw.startswith("3 "):
            arg = raw[2:].strip() if raw.startswith("3 ") else "list"
            self.do_modules(arg)
            return False
        elif raw == "4" or raw.startswith("4 "):
            arg = raw[2:].strip() if raw.startswith("4 ") else "list"
            self.do_secrets(arg)
            return False
        elif raw == "5" or raw.startswith("5 "):
            arg = raw[2:].strip() if raw.startswith("5 ") else "map"
            self.do_vault(arg)
            return False
        elif raw == "6":
            self.do_update("")
            return False
        elif raw == "7":
            self.do_info("")
            return False
        elif raw in ("0", "q"):
            return self.do_exit("")

        if raw.startswith("!"):
            os.system(raw[1:].strip())
            return False

        try:
            from nexgen_core.cli import _run_cli
            args = shlex.split(raw, posix=sys.platform != "win32")
            _run_cli(args)
        except Exception as exc:
            print(f"{C_YELLOW}[!] Command error: {exc}{C_RESET}")
        return False

    def do_info(self, arg: str) -> None:
        """Show visual system architecture and health dashboard."""
        as_json = "--json" in arg
        print(render_info(as_json=as_json, vault_data=self.vault_data))

    def do_status(self, arg: str) -> None:
        """Alias for info."""
        self.do_info(arg)

    def do_doctor(self, arg: str) -> None:
        """Run health diagnostics. Options: -v (verbose), --fix (apply remedies), --strict."""
        from nexgen_core.cli.engine import cmd_doctor
        import argparse
        p = argparse.ArgumentParser(prog="doctor")
        p.add_argument("-v", "--verbose", action="store_true")
        p.add_argument("--strict", action="store_true")
        p.add_argument("--json", action="store_true")
        p.add_argument("--summary", action="store_true")
        p.add_argument("--fix", action="store_true")
        try:
            parsed = p.parse_args(shlex.split(arg))
            cmd_doctor(parsed)
        except SystemExit:
            pass

    def do_sync(self, arg: str) -> None:
        """Realign configurations and materializations across all runtimes."""
        from nexgen_core.cli.engine import cmd_sync
        import argparse
        p = argparse.ArgumentParser(prog="sync")
        p.add_argument("--offline", action="store_true")
        p.add_argument("--skip-mcp", action="store_true")
        try:
            parsed = p.parse_args(shlex.split(arg))
            setattr(parsed, "mode", "apply")
            cmd_sync(parsed)
        except SystemExit:
            pass

    def do_modules(self, arg: str) -> None:
        """Manage engine modules (memory, firecrawl, ocr, n8n, browser, council, sync).
        Usage: modules list
               modules set <id> <absent|local|remote>"""
        from nexgen_core.cli import _run_cli
        args = ["modules"] + (shlex.split(arg) if arg else ["list"])
        _run_cli(args)

    def complete_modules(self, text: str, line: str, begidx: int, endidx: int) -> list[str]:
        tokens = line.split()
        if len(tokens) <= 2 and not (len(tokens) == 2 and line.endswith(" ")):
            return [c for c in self.MODULE_SUBCMDS if c.startswith(text)]
        return []

    def do_secrets(self, arg: str) -> None:
        """Manage age secrets store.
        Usage: secrets list | materialize | doctor | get <name> | set <name> | set-oauth <provider>"""
        from nexgen_core.paths import resolve_vault_data
        vault = resolve_vault_data(resolve_home(), self.vault_data)
        script = vault / "03-INFRA" / "scripts" / "nexgen-secrets.py"
        if not script.is_file():
            print(f"{C_YELLOW}[!] Secrets manager script not found: {script}{C_RESET}")
            return
        arg_str = arg if arg else "list"
        os.system(f"{sys.executable} {shlex.quote(str(script))} {arg_str}")

    def complete_secrets(self, text: str, line: str, begidx: int, endidx: int) -> list[str]:
        tokens = line.split()
        if len(tokens) <= 2 and not (len(tokens) == 2 and line.endswith(" ")):
            return [c for c in self.SECRETS_SUBCMDS if c.startswith(text)]
        return []

    def do_vault(self, arg: str) -> None:
        """Vault operations: map, push."""
        from nexgen_core.cli import _run_cli
        args = ["vault"] + (shlex.split(arg) if arg else ["map"])
        _run_cli(args)

    def complete_vault(self, text: str, line: str, begidx: int, endidx: int) -> list[str]:
        tokens = line.split()
        if len(tokens) <= 2 and not (len(tokens) == 2 and line.endswith(" ")):
            return [c for c in self.VAULT_SUBCMDS if c.startswith(text)]
        return []

    def do_update(self, arg: str) -> None:
        """Update NeXgen Engine release."""
        from nexgen_core.cli import _run_cli
        args = ["update"] + shlex.split(arg)
        _run_cli(args)

    def do_clear(self, arg: str) -> None:
        """Clear the terminal screen and reprint status header."""
        os.system("cls" if sys.platform == "win32" else "clear")
        print(render_info(vault_data=self.vault_data))
        print(render_menu())
        print()

    def do_help(self, arg: str) -> None:
        """Display the selectable actions menu and usage help."""
        print()
        print(render_menu())
        print(f"  {C_DIM}Tip: Enter a number [1-7, 0] or type command with arguments (e.g. 'doctor -v'){C_RESET}")
        print()

    def do_exit(self, arg: str) -> bool:
        """Exit the NeXgen interactive shell."""
        print(f"{C_SLATE}Exiting NeXgen Shell. Stay in sync.{C_RESET}")
        return True

    def do_quit(self, arg: str) -> bool:
        """Alias for exit."""
        return self.do_exit(arg)

    def completenames(self, text: str, *ignored: Any) -> list[str]:
        return [c for c in self.COMMANDS if c.startswith(text)]


def run_shell(vault_data: Path | None = None) -> int:
    """Launches the interactive NeXgen Shell session."""
    print(render_info(vault_data=vault_data))
    print(render_menu())
    print()
    try:
        shell = NeXgenShell(vault_data=vault_data)
        shell.cmdloop()
        return 0
    except (KeyboardInterrupt, EOFError):
        print(f"\n{C_SLATE}Session ended.{C_RESET}")
        return 0
