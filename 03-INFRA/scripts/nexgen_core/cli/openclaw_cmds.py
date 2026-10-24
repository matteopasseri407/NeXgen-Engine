"""`nexgen openclaw`: the harness with the interface.

`plan` says what the engine would configure and what stays the person's;
`setup` applies exactly that plan, with a backup and a way back; `doctor`
proves a live configuration is one OpenClaw can actually use; `stop` and
`restart` take the gateway down to edit its file by hand and bring it back;
`pair` lets a phone, or another machine, in; `node` proposes the commands that
make this machine a node of a gateway elsewhere.

Every verb that touches the machine lives in a module of its own; this file
only declares the arguments and prints what comes back.
"""
from __future__ import annotations

import json

from nexgen_core.i18n import t


def _usage(parser) -> int:
    parser.print_help()
    return 0

def _register(sub) -> None:
    """The `openclaw` verb, and the three families its arguments fall into."""
    p = sub.add_parser("openclaw", help=t("OpenClaw, the harness with the interface"))
    psub = p.add_subparsers(dest="openclaw_command", metavar="verb")
    _register_setup(psub)
    _register_pairing(psub)
    _register_upkeep(psub)
    p.set_defaults(func=lambda args: _usage(p))


def _register_setup(psub) -> None:
    """What the engine would configure, the setup that applies it, and the doctor that judges it."""
    q = psub.add_parser("plan", help=t("Read-only: what the engine would configure, and what stays yours"))
    q.add_argument("--json", action="store_true", help=t("Output in JSON format"))
    q.add_argument("--lan", action="store_true",
                   help=t("Also reachable from the home network (the phone app)"))
    q.add_argument("--acpx", action="store_true", help=t("Allow the acpx plugin too"))
    q.set_defaults(func=cmd_plan)

    q = psub.add_parser("setup", help=t("Install, configure, write the agent manuals and start the gateway"))
    q.add_argument("--yes", action="store_true", help=t("Do not ask for confirmation"))
    q.add_argument("--lan", action="store_true",
                   help=t("Also reachable from the home network (the phone app)"))
    q.add_argument("--dry-run", action="store_true",
                   help=t("Print the plan and the commands, and change nothing"))
    q.add_argument("--no-sviluppo", dest="no_sviluppo", action="store_true",
                   help=t("Do not create the agent that runs OpenCode through ACP"))
    q.add_argument("--node-gateway", dest="node_gateway", action="store_true",
                   help=t("Go on with the native gateway even when the Windows app runs its own on this PC"))
    q.set_defaults(func=cmd_setup)

    q = psub.add_parser("doctor", help=t("Check OpenClaw's installation and configuration"))
    q.add_argument("--json", action="store_true", help=t("Output in JSON format"))
    q.add_argument("--nodes", action="store_true",
                   help=t("Also list the nodes the running gateway knows (starts OpenClaw's CLI once)"))
    q.set_defaults(func=cmd_doctor)


def _register_pairing(psub) -> None:
    """Letting a phone, or another machine, in: the two stages of pairing, and a node's commands."""
    q = psub.add_parser("pair", help=t("List the phones and the nodes waiting to be paired, and approve one"))
    q.add_argument("--approve", metavar="ID", default=None, help=t("Approve the device request with this id"))
    q.add_argument("--approve-node", dest="approve_node", metavar="ID", default=None,
                   help=t("Approve the node request with this id, showing the commands it exposes"))
    q.add_argument("--yes", action="store_true", help=t("Do not ask for confirmation"))
    q.set_defaults(func=cmd_pair)

    q = psub.add_parser("node", help=t("Connect this machine as a node of a gateway on another PC"))
    q.add_argument("--host", required=True, metavar="ADDRESS",
                   help=t("The gateway machine's address on the home network"))
    q.add_argument("--port", type=int, default=None, metavar="PORT",
                   help=t("The gateway's port (default: 18789)"))
    q.add_argument("--name", default=None, metavar="LABEL",
                   help=t("The name the gateway shows for this machine"))
    q.set_defaults(func=cmd_node)


def _register_upkeep(psub) -> None:
    """What OpenClaw created and the engine adopts, and taking the gateway down and back."""
    q = psub.add_parser("adopt", help=t("Adopt what OpenClaw created into the canonical library"))
    q.add_argument("--skills", action="store_true", help=t("Only the skills the Skill Workshop learned"))
    q.add_argument("--mcp", action="store_true", help=t("Only the MCP servers added in OpenClaw"))
    q.add_argument("--dry-run", action="store_true",
                   help=t("Show the plan and the checks, and change nothing"))
    q.add_argument("--yes", action="store_true",
                   help=t("Do not ask: adopt what is safe, and leave what needs a person"))
    q.set_defaults(func=cmd_adopt)

    q = psub.add_parser("restart", help=t("Restart the gateway and wait for its port"))
    q.add_argument("--timeout", type=float, default=None, metavar="SECONDS",
                   help=t("How long to wait for the port (default: 600)"))
    q.set_defaults(func=cmd_restart)

    q = psub.add_parser("stop", help=t("Stop the gateway before editing its file by hand"))
    q.set_defaults(func=cmd_stop)

    q = psub.add_parser("rpc", help=t("Interact with OpenClaw Gateway via WebSocket RPC"))
    rpc_sub = q.add_subparsers(dest="rpc_command", metavar="action")

    r = rpc_sub.add_parser("health", help=t("Check OpenClaw Gateway health snapshot via WebSocket RPC"))
    r.add_argument("--json", action="store_true", help=t("Output in JSON format"))
    r.add_argument("--url", default=None, metavar="URL", help=t("Override gateway WebSocket URL"))
    r.add_argument("--token", default=None, metavar="TOKEN", help=t("Override gateway authentication token"))
    r.add_argument("--timeout", type=float, default=10.0, metavar="SECONDS",
                   help=t("How long to wait for response (default: 10)"))
    r.set_defaults(func=cmd_rpc_health)
    q.set_defaults(func=lambda args: _usage(q))


def register(sub) -> None:
    """Declares the OpenClaw verbs on the dispatcher."""
    _register(sub)


def cmd_plan(args) -> int:
    from nexgen_core.openclaw.plan import build_plan, render_plan
    from nexgen_core.openclaw.profile import Choices

    plan = build_plan(choices=Choices(lan=bool(args.lan), acpx=bool(args.acpx)))
    if getattr(args, "json", False):
        print(json.dumps(plan.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(render_plan(plan))
    return 0


def cmd_setup(args) -> int:
    from nexgen_core.openclaw.setup import run_setup

    return run_setup(
        yes=bool(args.yes),
        lan=bool(args.lan),
        dry_run=bool(args.dry_run),
        sviluppo=not bool(args.no_sviluppo),
        node_gateway=bool(args.node_gateway),
    )


def cmd_doctor(args) -> int:
    from nexgen_core.openclaw import node_checks
    from nexgen_core.openclaw.doctor import collect, exit_code, render
    from nexgen_core.openclaw_pairing import listed as gateway_devices

    lister = node_checks.default_node_lister if getattr(args, "nodes", False) else None
    outcomes = collect(node_lister=lister, pending=gateway_devices)
    if getattr(args, "json", False):
        print(json.dumps([outcome.to_dict() for outcome in outcomes], indent=2, ensure_ascii=False))
    else:
        print(render(outcomes))
    return exit_code(outcomes)


def cmd_adopt(args) -> int:
    from nexgen_core.openclaw.adopt import run_adopt

    wants_skills = bool(getattr(args, "skills", False))
    wants_mcp = bool(getattr(args, "mcp", False))
    both = not wants_skills and not wants_mcp
    return run_adopt(
        skills=wants_skills or both,
        mcp=wants_mcp or both,
        dry_run=bool(getattr(args, "dry_run", False)),
        yes=bool(getattr(args, "yes", False)),
    )


def cmd_restart(args) -> int:
    from nexgen_core.openclaw.setup import START_TIMEOUT_SECONDS, run_restart

    timeout = float(args.timeout) if args.timeout is not None else START_TIMEOUT_SECONDS
    return run_restart(timeout_s=timeout)


def cmd_stop(args) -> int:
    from nexgen_core.openclaw.setup import run_stop

    return run_stop()


def cmd_pair(args) -> int:
    from nexgen_core.openclaw.setup import run_pair

    return run_pair(
        approve_id=args.approve,
        approve_node_id=args.approve_node,
        yes=bool(args.yes),
    )


def cmd_node(args) -> int:
    from nexgen_core.openclaw.node_link import build_node_link, render_node_link
    from nexgen_core.openclaw.profile import GATEWAY_PORT

    port = int(args.port) if args.port is not None else GATEWAY_PORT
    try:
        link = build_node_link(args.host, port=port, name=args.name)
    except ValueError as error:
        print(t("Cannot propose the node connection: {error}", error=error))
        return 2
    print(render_node_link(link))
    return 0


def cmd_rpc_health(args) -> int:
    import sys
    from nexgen_core.openclaw.gateway_rpc import GatewayRpcError, health

    url = getattr(args, "url", None)
    token = getattr(args, "token", None)
    timeout = float(getattr(args, "timeout", 10.0) or 10.0)
    as_json = bool(getattr(args, "json", False))

    try:
        res = health(url=url, token=token, timeout=timeout)
        if as_json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            print(t("OpenClaw Gateway RPC: healthy"))
            if isinstance(res, dict):
                uptime = res.get("uptimeMs")
                if uptime is not None:
                    print(f"  Uptime: {uptime} ms")
                plugins = res.get("plugins")
                if isinstance(plugins, dict) and "loaded" in plugins:
                    print(f"  Plugins loaded: {len(plugins['loaded'])}")
        return 0
    except GatewayRpcError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
