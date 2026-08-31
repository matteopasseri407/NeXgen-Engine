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

    q = tsub.add_parser("update-notifier", help=t("Check for engine updates and display native UI prompt"))
    q.add_argument("--force", action="store_true", help=t("Ignore time throttle and prompt if update available"))
    q.add_argument("--demo", action="store_true", help=t("Simulate prompt dialog for test"))
    q.add_argument("--install-autostart", action="store_true", help=t("Configure user autostart"))
    q.set_defaults(func=lambda a: _run("update_notifier", _all(a)))

    p.set_defaults(func=lambda a: _usage(p))

    # `mcp` sta a livello top-level: è la superficie che rulesync ha reso
    # familiare e il piano chiede per nome (`nexgen mcp add`).
    m = sub.add_parser("mcp", help=t("Add and inspect MCP connectors"))
    msub = m.add_subparsers(dest="mcp_command", metavar="verb")

    a = msub.add_parser("add", help=t("Add one server to the manifest: validated, atomic, with backup"))
    a.add_argument("name", help=t("Server name in the manifest"))
    a.add_argument("--targets", required=True,
                   help=t("Comma-separated CLIs (claude,codex,antigravity,opencode) or 'all'"))
    a.add_argument("--command", dest="server_command",
                   help=t("Stdio command (e.g. npx); mutually exclusive with --url"))
    a.add_argument("--args", action="append", help=t("One argument per flag; repeatable. Use --args=-value for values starting with a dash"))
    a.add_argument("--url", help=t("http(s) URL of a streamable-http server; mutually exclusive with --command"))
    a.add_argument("--auth-env", dest="auth_env", help=t("Name of the env var carrying the bearer token (the manifest never stores the token)"))
    a.add_argument("--env", action="append", help=t("Environment entry KEY=VALUE for a stdio server; values must be ${VAR} references when secret-shaped"))
    a.add_argument("--lazy", action="store_true", help=t("Serve through the lazy proxy instead of mounting directly"))
    a.add_argument("--readonly", action="store_true", help=t("Allowlist every tool of this (lazy) server as read-only"))
    a.add_argument("--dry-run", dest="dry_run", action="store_true", help=t("Print the stub instead of writing"))
    a.set_defaults(func=cmd_mcp_add)

    a = msub.add_parser("list", help=t("Read-only: the servers per CLI as they would render now"))
    a.set_defaults(func=cmd_mcp_list)

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


def cmd_mcp_add(args) -> int:
    from nexgen_core.mcp_add import add_server

    code, message = add_server(
        args.name,
        args.targets,
        command=getattr(args, "server_command", None),
        args=list(args.args or []),
        url=getattr(args, "url", None),
        auth_env=getattr(args, "auth_env", None),
        env_pairs=list(args.env or []),
        lazy=getattr(args, "lazy", False),
        readonly=getattr(args, "readonly", False),
        dry_run=getattr(args, "dry_run", False),
    )
    print(message)
    return code


def cmd_mcp_list(args) -> int:
    from nexgen_core.renderer_cli import cmd_inventory

    return cmd_inventory()
