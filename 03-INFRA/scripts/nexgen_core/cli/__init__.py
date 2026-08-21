#!/usr/bin/env python3
"""The engine's single entry point: `nexgen`.

There used to be thirteen separate commands with names inherited from
different versions. Now there's one verb for everything a person might ask
for, and the old names keep working as aliases, because an already-installed
machine invokes them by path and breaking them means breaking it mid-update.

The verb groups live in separate modules and register here. Adding a command
means adding a line in a module, not touching this file.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import __version__
from nexgen_core.cli import engine, skill_cmds, stack_cmds, tool_cmds, vault_cmds
from nexgen_core.i18n import set_language, t

#: The verb groups, in the order they appear in the help text.
GROUPS = (engine, vault_cmds, skill_cmds, stack_cmds, tool_cmds)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nexgen",
        description=t("NeXgen Engine — keeps your machines and your assistants aligned."),
    )
    # The language comes from the system. This forces it, for anyone working
    # on a machine set up in someone else's language, or who wants the source.
    parser.add_argument(
        "--lang", metavar="CODE", default=None,
        help=t("Force the language of the messages (for example: it, en)"),
    )
    parser.add_argument(
        "--version", action="version", version=f"NeXgen Engine {__version__}",
        help=t("Show the engine version and exit"),
    )
    sub = parser.add_subparsers(dest="command", metavar="command")
    for group in GROUPS:
        group.register(sub)
    return parser


def _run_cli(argv: list[str]) -> int:
    parser = build_parser()
    # Arguments meant for a subcommand are not this parser's business: it
    # collects them and hands them off intact to whoever forwards them.
    # Parsing them here would mean rejecting every flag the subcommand adds.
    args, passthrough = parser.parse_known_args(argv)
    args.passthrough = passthrough
    if getattr(args, "lang", None):
        set_language(args.lang)

    if not getattr(args, "command", None):
        parser.print_help()
        return 0

    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 0
    return func(args)


def main(argv: list[str] | None = None) -> int:
    """Entry point, with a safety net: an error never prints a traceback."""
    if argv is None:
        argv = sys.argv[1:]
    try:
        return _run_cli(argv)
    except KeyboardInterrupt:
        print("\n" + t("Interrupted."), file=sys.stderr)
        return 130
    except BrokenPipeError:
        return 0
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
