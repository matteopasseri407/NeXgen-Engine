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
    q.add_argument("--format", choices=["json", "human", "shell"], default="json")
    q.add_argument("--json", action="store_true", help=t("Output in JSON"))
    q.add_argument("--human", action="store_true", help=t("Output human-readable"))
    q.add_argument("--shell", action="store_true", help=t("Output for shell eval"))
    q.set_defaults(func=cmd_now)

    q = tsub.add_parser("open", help=t("Open a folder in the system's file manager"))
    q.add_argument("path", help=t("Folder path to open"))
    q.set_defaults(func=cmd_open)

    q = tsub.add_parser("chrome", help=t("Start or revive Chrome on the debug port"))
    q.add_argument("--ensure", action="store_true", help=t("Exit 0 if CDP port is open, otherwise launch Chrome"))
    q.add_argument("--heal", action="store_true", help=t("Restart Chrome if process exists without CDP port"))
    q.add_argument("args", nargs="*", help=t("Additional Chrome arguments or URLs"))
    q.set_defaults(func=cmd_chrome)

    q = tsub.add_parser("firecrawl", help=t("Search and scraping via the local instance"))
    fsub = q.add_subparsers(dest="firecrawl_action", metavar="action")

    qs = fsub.add_parser("status", help=t("Check the service status"))
    qs.set_defaults(func=lambda a: _run("firecrawl", ["status"]))

    qs = fsub.add_parser("scrape", help=t("Scrape a URL"))
    qs.add_argument("url", help=t("URL to fetch"))
    qs.add_argument("-f", "--format", default="markdown", help=t("Comma-separated formats (markdown, links)"))
    qs.add_argument("--json", action="store_true", help=t("Raw JSON output"))
    qs.add_argument("-o", "--output", help=t("Save the output to a file"))
    qs.set_defaults(func=_forward_firecrawl_scrape)

    qs = fsub.add_parser("search", help=t("Run a web search"))
    qs.add_argument("query", nargs="+", help=t("Search terms"))
    qs.add_argument("--limit", type=int, default=20, help=t("Maximum number of results"))
    qs.add_argument("--sources", help=t("Comma-separated sources (web, news, images)"))
    qs.add_argument("--scrape", action="store_true", help=t("Also fetch the content of the results"))
    qs.add_argument("--scrape-formats", default="markdown", help=t("Scraping formats"))
    qs.add_argument("--json", action="store_true", help=t("Raw JSON output"))
    qs.add_argument("-o", "--output", help=t("Save the output to a file"))
    qs.set_defaults(func=_forward_firecrawl_search)

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


def cmd_now(args) -> int:
    from nexgen_core.tools.now import main as now_main

    argv = []
    if getattr(args, "json", False):
        argv.append("--json")
    elif getattr(args, "human", False):
        argv.append("--human")
    elif getattr(args, "shell", False):
        argv.append("--shell")
    elif getattr(args, "format", None) and args.format != "json":
        argv.extend(["--format", args.format])
    argv.extend(_all(args))
    return now_main(argv)


def cmd_open(args) -> int:
    from nexgen_core.tools.open_folder import main as open_main

    argv = [args.path] if getattr(args, "path", None) else []
    argv.extend(_all(args))
    return open_main(argv)


def cmd_chrome(args) -> int:
    from nexgen_core.tools.chrome import main as chrome_main

    argv = []
    if getattr(args, "ensure", False):
        argv.append("--ensure")
    if getattr(args, "heal", False):
        argv.append("--heal")
    if getattr(args, "args", None):
        argv.extend(args.args)
    argv.extend(_all(args))
    return chrome_main(argv)


def _forward_firecrawl_scrape(args) -> int:
    from nexgen_core.tools.firecrawl import main as fc_main

    argv = ["scrape", args.url]
    if getattr(args, "format", None):
        argv.extend(["-f", args.format])
    if getattr(args, "json", False):
        argv.append("--json")
    if getattr(args, "output", None):
        argv.extend(["-o", args.output])
    argv.extend(_all(args))
    return fc_main(argv)


def _forward_firecrawl_search(args) -> int:
    from nexgen_core.tools.firecrawl import main as fc_main

    argv = ["search", *args.query]
    if getattr(args, "limit", None) is not None:
        argv.extend(["--limit", str(args.limit)])
    if getattr(args, "sources", None):
        argv.extend(["--sources", args.sources])
    if getattr(args, "scrape", False):
        argv.append("--scrape")
    if getattr(args, "scrape_formats", None):
        argv.extend(["--scrape-formats", args.scrape_formats])
    if getattr(args, "json", False):
        argv.append("--json")
    if getattr(args, "output", None):
        argv.extend(["-o", args.output])
    argv.extend(_all(args))
    return fc_main(argv)


def cmd_council(args) -> int:
    from nexgen_core.tools.council import main as council_main

    return council_main(_all(args))
