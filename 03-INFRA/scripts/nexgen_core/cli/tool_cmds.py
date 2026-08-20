"""The tools: the real time, a folder to open, the browser, search.

These are small commands the engine ships because agents use them. Each
stays its own module: this is just the hook into the dispatcher.
"""
from __future__ import annotations

from nexgen_core.i18n import t


def _all(args) -> list[str]:
    """The arguments the dispatcher didn't recognize, in their original order."""
    return list(getattr(args, "passthrough", []) or [])


def register(sub) -> None:
    p = sub.add_parser("tool", help=t("Tools shipped with the engine"))
    tsub = p.add_subparsers(dest="tool_command", metavar="tool")

    q = tsub.add_parser("now", help=t("The trustworthy local time, with sync status"))
    q.set_defaults(func=lambda a: _run("now", _all(a)))

    q = tsub.add_parser("open", help=t("Open a folder in the system's file manager"))
    q.set_defaults(func=lambda a: _run("open_folder", _all(a)))

    q = tsub.add_parser("chrome", help=t("Start or revive Chrome on the debug port"))
    q.set_defaults(func=lambda a: _run("chrome", _all(a)))

    q = tsub.add_parser("firecrawl", help=t("Search and scraping via the local instance"))
    q.set_defaults(func=lambda a: _run("firecrawl", _all(a)))

    p.set_defaults(func=lambda a: _usage(p))

    # The council isn't a tool: it's its own kind of request, and stays top-level.
    c = sub.add_parser("council", help=t("Convene a review across models from different vendors"))
    c.set_defaults(func=cmd_council)


def _usage(parser) -> int:
    parser.print_help()
    return 0


def _run(module_name: str, argv: list[str]) -> int:
    import importlib

    module = importlib.import_module(f"nexgen_core.tools.{module_name}")
    return module.main(argv)


def cmd_council(args) -> int:
    from nexgen_core.tools.council import main as council_main

    return council_main(_all(args))
