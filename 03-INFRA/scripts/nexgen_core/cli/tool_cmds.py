"""The tools: the real time, a folder to open, the browser.

These are small commands the engine ships because agents use them. Each
stays its own module: this is just the hook into the dispatcher.
"""
from __future__ import annotations

import sys

from nexgen_core.i18n import t


def _all(args) -> list[str]:
    """The arguments the dispatcher didn't recognize, in their original order."""
    return list(getattr(args, "passthrough", []) or [])


def _register_utilities(sub) -> None:
    """The small tools, and the councils, which live on the top-level dispatcher."""
    p = sub.add_parser("tool", aliases=["tools"], help=t("Tools shipped with the engine"))
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

    _register_update_notifier(tsub)
    p.set_defaults(func=lambda a: _usage(p))

    # The council isn't a tool: it's its own top-level verb. The runner is
    # the launcher in nexgen_core/tools/council.py, which subprocesses
    # agent-universal-layer/council/council.py where the logic lives.
    c = sub.add_parser("council", help=t("Convene a review across models from different vendors"))
    c.set_defaults(func=cmd_council)


def _register_update_notifier(tsub) -> None:
    """The update notice: the check, the prompt, and where it is installed."""
    q = tsub.add_parser("update-notifier", help=t("Check for engine updates and display native UI prompt"))
    q.add_argument("--force", action="store_true", help=t("Ignore time throttle and prompt if update available"))
    q.add_argument("--demo", action="store_true", help=t("Simulate prompt dialog for test"))
    q.add_argument("--install-autostart", action="store_true", help=t("Configure user autostart"))
    q.add_argument("--install-shell-hook", action="store_true", help=t("Install the shell startup notice"))
    q.add_argument("--remove", action="store_true", help=t("With an install flag: remove instead of installing"))
    q.add_argument("--shell", choices=["bash", "powershell"], default=None, help=t("With shell hook: only this shell"))
    q.set_defaults(func=cmd_update_notifier)


def _register_mcp_block(sub) -> None:
    """`nexgen mcp`: the connectors, from adding one to judging what a CLI actually loads."""
    # `mcp` sta a livello top-level: è la superficie che rulesync ha reso
    # familiare e il piano chiede per nome (`nexgen mcp add`).
    m = sub.add_parser("mcp", help=t("Add and inspect MCP connectors"))
    msub = m.add_subparsers(dest="mcp_command", metavar="verb")

    _register_mcp_add(msub)
    _register_mcp_trials(msub)
    _register_mcp_inspection(msub)


def _register_mcp_add(msub) -> None:
    """One server into the manifest: validated, atomic, with a backup beside it."""
    a = msub.add_parser("add", help=t("Add one server to the manifest: validated, atomic, with backup"))
    a.add_argument("name", help=t("Server name in the manifest"))
    a.add_argument("--targets", default="all",
                   help=t("Comma-separated CLIs (claude,codex,antigravity,opencode) or 'all' (the default)"))
    a.add_argument("--command", dest="server_command",
                   help=t("Stdio command (e.g. npx); mutually exclusive with --url"))
    a.add_argument("--args", action="append", help=t("One argument per flag; repeatable. Use --args=-value for values starting with a dash"))
    a.add_argument("--url", help=t("http(s) URL of a streamable-http server; mutually exclusive with --command"))
    a.add_argument("--auth-env", dest="auth_env", help=t("Name of the env var carrying the bearer token (the manifest never stores the token)"))
    a.add_argument("--env", action="append", help=t("Environment entry KEY=VALUE for a stdio server; values must be ${VAR} references when secret-shaped"))
    a.add_argument("--lazy", action="store_true", help=t("Serve through the gateway (this is the default; accepted for older scripts)"))
    a.add_argument("--eager", action="store_true", help=t("Mount it directly in every CLI instead: for the few servers that must always be there"))
    a.add_argument("--readonly", action="store_true", help=t("Allowlist every tool of this (lazy) server as read-only"))
    a.add_argument("--dry-run", dest="dry_run", action="store_true", help=t("Print the stub instead of writing"))
    a.set_defaults(func=cmd_mcp_add)


def _register_mcp_trials(msub) -> None:
    """The trials: try a server here, list them, keep one, end one."""
    def _server_args(p):
        p.add_argument("name", help=t("Server name"))
        p.add_argument("--command", dest="server_command", help=t("Stdio command (e.g. npx); mutually exclusive with --url"))
        p.add_argument("--args", action="append", help=t("One argument per flag; repeatable. Use --args=-value for values starting with a dash"))
        p.add_argument("--url", help=t("http(s) URL of a streamable-http server; mutually exclusive with --command"))
        p.add_argument("--auth-env", dest="auth_env", help=t("Name of the env var carrying the bearer token"))
        p.add_argument("--env", action="append", help=t("Environment entry KEY=VALUE for a stdio server; secret-shaped values must be ${VAR} references"))
        p.add_argument("--targets", default="all", help=t("Comma-separated CLIs or 'all' (the default)"))
        p.add_argument("--readonly", action="store_true", help=t("Allowlist every tool of this server as read-only"))

    a = msub.add_parser("try", help=t("Try a server on this machine only: served by the gateway, not synced, gone by itself after --for hours"))
    _server_args(a)
    a.add_argument("--for", dest="hours", type=float, default=24.0, help=t("How many hours the trial lasts (default 24, at most 168)"))
    a.set_defaults(func=cmd_mcp_try)

    a = msub.add_parser("trials", help=t("Read-only: the servers on trial here and how long they have left"))
    a.set_defaults(func=lambda args: __import__("nexgen_core.mcp_trials", fromlist=["cmd_trials"]).cmd_trials())


def _register_mcp_inspection(msub) -> None:
    """Read-only views, and the two that start a backend to compare what a CLI serves."""
    a = msub.add_parser("promote", help=t("Keep a trial: write it into the manifest, where it syncs to every machine"))
    a.add_argument("name")
    a.set_defaults(func=cmd_mcp_promote)

    a = msub.add_parser("drop", help=t("End a trial now"))
    a.add_argument("name")
    a.set_defaults(func=cmd_mcp_drop)

    a = msub.add_parser("list", help=t("Read-only: the servers per CLI as they would render now"))
    a.set_defaults(func=cmd_mcp_list)

    a = msub.add_parser("bump", help=t("Raise guardian-vetted pins (the same command as `skills bump`), trying each server before keeping it"))
    a.set_defaults(func=cmd_mcp_bump)

    a = msub.add_parser("plan", help=t("Read-only: which server is mounted directly, behind the gateway, or absent, per CLI, and why"))
    a.add_argument("--json", action="store_true", help=t("Machine-readable output"))
    a.set_defaults(func=cmd_mcp_plan)

    a = msub.add_parser("check", help=t("Start each CLI's gateway as that CLI would and compare what it serves with the plan (starts the backends)"))
    a.add_argument("--cli", choices=("claude", "codex", "antigravity", "opencode", "openclaw"), help=t("Only this CLI"))
    a.add_argument("--timeout", type=float, default=90.0, help=t("Seconds to wait for the gateway's index (backends are started to build it)"))
    a.add_argument("--direct", action="store_true", help=t("Instead: measure the directly mounted servers (tools and weight of what a CLI loads up front)"))
    a.set_defaults(func=cmd_mcp_check)

    _register_mcp_bridge(msub)


def _register_mcp_bridge(msub) -> None:
    """The bridge, for a gateway that cannot launch this machine's stdio connectors itself."""
    from nexgen_core.mcp_bridge import DEFAULT_PORT

    b = msub.add_parser("bridge", help=t("Serve this machine's stdio connectors over local HTTP, for a gateway that cannot launch them"))
    bsub = b.add_subparsers(dest="bridge_command", metavar="action")

    start = bsub.add_parser("start", help=t("Start the bridge: loopback by default, and never on its own"))
    start.add_argument("--host", help=t("Address to bind (default: loopback; 'wsl' for the virtual WSL interface)"))
    start.add_argument("--wsl", action="store_true", help=t("Bind the virtual WSL interface, the address the distro can reach"))
    start.add_argument("--port", type=int, default=DEFAULT_PORT, help=t("Port to listen on (default: {port})", port=DEFAULT_PORT))
    start.add_argument("--server", action="append", dest="servers",
                       help=t("Bridge only this connector; repeatable (default: every stdio connector the OpenClaw gateway mounts)"))
    start.add_argument("--foreground", action="store_true", help=t("Stay in this terminal instead of detaching"))
    start.set_defaults(func=cmd_mcp_bridge_start)

    stop = bsub.add_parser("stop", help=t("Ask the running bridge to stop"))
    stop.set_defaults(func=cmd_mcp_bridge_stop)

    status = bsub.add_parser("status", help=t("Where the bridge listens and which connectors it serves"))
    status.set_defaults(func=cmd_mcp_bridge_status)

    b.set_defaults(func=lambda a: _usage(b))


def register(sub) -> None:
    _register_utilities(sub)
    _register_mcp_block(sub)
    _register_quota(sub)


def _register_quota(sub) -> None:
    """What is left of each subscription, and how old the reading is.

    Its own verb because it answers a question about the person's money and
    plans, not about the engine's tools.
    """
    q = sub.add_parser("quota", help=t("What is left of each subscription, and how old the reading is"))
    q.add_argument("--json", action="store_true", help=t("Output in JSON format"))
    q.set_defaults(func=cmd_quota)


def _usage(parser) -> int:
    parser.print_help()
    return 0


def _run(module_name: str, argv: list[str]) -> int:
    import importlib

    module = importlib.import_module(f"nexgen_core.tools.{module_name}")
    return module.main(argv)


def cmd_update_notifier(args) -> int:
    from nexgen_core.tools import update_notifier

    argv = list(_all(args))
    for flag, token in (("force", "--force"), ("demo", "--demo"),
                        ("install_autostart", "--install-autostart"),
                        ("install_shell_hook", "--install-shell-hook"),
                        ("remove", "--remove")):
        if getattr(args, flag, False) and token not in argv:
            argv.append(token)
    if getattr(args, "shell", None):
        argv.extend(["--shell", args.shell])
    return update_notifier.main(argv)


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


def cmd_council(args) -> int:
    from nexgen_core.tools.council import main as council_main

    return council_main(_all(args))


def cmd_quota(args) -> int:
    from nexgen_core import quota

    return quota.main(as_json=getattr(args, "json", False))


def cmd_mcp_add(args) -> int:
    from nexgen_core.mcp_add import add_server

    if getattr(args, "eager", False) and getattr(args, "lazy", False):
        print(t("--eager and --lazy contradict each other: pick one (lazy is the default)"))
        return 2

    code, message = add_server(
        args.name,
        args.targets,
        command=getattr(args, "server_command", None),
        args=list(args.args or []),
        url=getattr(args, "url", None),
        auth_env=getattr(args, "auth_env", None),
        env_pairs=list(args.env or []),
        lazy=not getattr(args, "eager", False),
        readonly=getattr(args, "readonly", False),
        dry_run=getattr(args, "dry_run", False),
    )
    print(message)
    return code


def cmd_mcp_try(args) -> int:
    from nexgen_core.mcp_trials import cmd_try

    code, message = cmd_try(
        args.name, targets_raw=args.targets, command=getattr(args, "server_command", None), args=list(args.args or []),
        url=getattr(args, "url", None), auth_env=getattr(args, "auth_env", None), env_pairs=list(args.env or []),
        readonly=getattr(args, "readonly", False), hours=args.hours,
    )
    print(message)
    return code


def cmd_mcp_promote(args) -> int:
    from nexgen_core.mcp_trials import cmd_promote

    code, message = cmd_promote(args.name)
    print(message)
    return code


def cmd_mcp_drop(args) -> int:
    from nexgen_core.mcp_trials import cmd_drop

    code, message = cmd_drop(args.name)
    print(message)
    return code


def cmd_mcp_bump(args) -> int:
    from nexgen_core.thirdparty_bump import bump_batch

    return bump_batch()


def cmd_mcp_plan(args) -> int:
    from nexgen_core import mcp_plan

    return mcp_plan.main(as_json=getattr(args, "json", False))


def cmd_mcp_check(args) -> int:
    from nexgen_core import mcp_check

    clis = (args.cli,) if getattr(args, "cli", None) else mcp_check.CLIS
    if getattr(args, "direct", False):
        return mcp_check.main_direct(clis, timeout=min(getattr(args, "timeout", 90.0), 60.0))
    return mcp_check.main(clis, timeout=getattr(args, "timeout", 90.0))


def cmd_mcp_list(args) -> int:
    from nexgen_core.renderer_cli import cmd_inventory

    return cmd_inventory()


def cmd_mcp_bridge_start(args) -> int:
    from nexgen_core.mcp_bridge import BridgeError, start_bridge

    try:
        info = start_bridge(
            host=getattr(args, "host", None),
            port=getattr(args, "port", 8765),
            wsl=getattr(args, "wsl", False),
            names=getattr(args, "servers", None),
            foreground=getattr(args, "foreground", False),
        )
    except BridgeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print(t("MCP HTTP bridge stopped"))
        return 0
    if info.get("status") == "already_running":
        print(t("MCP HTTP bridge is already running: {url}", url=info.get("url", "")))
        return 0
    print(t("MCP HTTP bridge started: {url} (PID {pid})", url=info.get("url", ""), pid=info.get("pid", "?")))
    _print_bridge_connectors(info)
    return 0


def cmd_mcp_bridge_stop(args) -> int:
    from nexgen_core.mcp_bridge import stop_bridge

    result = stop_bridge()
    if result.get("stopped"):
        print(t("MCP HTTP bridge stopped"))
        return 0
    if result.get("reason") == "not running":
        print(t("MCP HTTP bridge is not running"))
        return 0
    print(t("MCP HTTP bridge is still answering at {url}: stop the process {pid} by hand",
            url=result.get("url", ""), pid=result.get("pid", "?")))
    return 1


def cmd_mcp_bridge_status(args) -> int:
    from nexgen_core.mcp_bridge import status_bridge

    status = status_bridge()
    if status.get("running"):
        print(t("MCP HTTP bridge is running: {url} (PID {pid})", url=status.get("url", ""), pid=status.get("pid", "?")))
        _print_bridge_connectors(status)
        return 0
    if status.get("stale"):
        print(t("MCP HTTP bridge is not running: the state file describes a run that ended"))
        return 1
    print(t("MCP HTTP bridge is not running"))
    return 1


def _print_bridge_connectors(info) -> None:
    """The names a gateway can address, so a person can copy one into a config."""
    servers = info.get("servers") or []
    if servers:
        print(t("Connectors: {servers}", servers=", ".join(str(name) for name in servers)))

