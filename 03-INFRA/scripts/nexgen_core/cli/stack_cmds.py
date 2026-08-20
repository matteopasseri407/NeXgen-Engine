"""The verbs on the local stack: start, stop, see how it's doing.

The full install is no longer just "get a server": these five connectors
can run on the machine of whoever's using them, and these commands are how
you get them without knowing what a tunnel is.
"""
from __future__ import annotations

from nexgen_core.i18n import t
from nexgen_core.paths import resolve_engine_root


def register(sub) -> None:
    p = sub.add_parser("stack", help=t("The connector services, on this machine"))
    ssub = p.add_subparsers(dest="stack_command", metavar="verb")

    q = ssub.add_parser("up", help=t("Start the services and configure the connectors"))
    q.add_argument("services", nargs="*", help=t("Only some services, instead of all of them"))
    q.set_defaults(func=cmd_up)

    q = ssub.add_parser("down", help=t("Stop the services, leaving the data intact"))
    q.add_argument("services", nargs="*")
    q.set_defaults(func=cmd_down)

    q = ssub.add_parser("status", help=t("Which services are responding"))
    q.set_defaults(func=cmd_status)

    q = ssub.add_parser("logs", help=t("The last lines of a service that won't start"))
    q.add_argument("service")
    q.add_argument("-n", "--lines", type=int, default=50)
    q.set_defaults(func=cmd_logs)

    p.set_defaults(func=lambda a: _usage(p))


def _usage(parser) -> int:
    parser.print_help()
    return 0


def cmd_up(args) -> int:
    from nexgen_core.stack import runner, secrets
    from nexgen_core.stack.services import SERVICES, env_file

    engine_root = resolve_engine_root()
    try:
        actions = runner.up(engine_root, args.services or None)
    except runner.StackError as exc:
        print(f"{exc}")
        return 1

    for action in actions:
        print(f"  ✓ {action}")

    # The connectors only mount themselves if the workstation knows where to find them.
    env_values = secrets.read_env_file(env_file(engine_root))
    exports: dict[str, str] = {}
    for service in SERVICES:
        if args.services and service.name not in args.services:
            continue
        exports.update(secrets.resolve_exports(service.exports, service.port, env_values))

    target = secrets.workstation_env_path()
    secrets.write_workstation_env(target, exports)
    print("  ✓ " + t("Connector variables written to {target}", target=target))
    print("\n" + t("Reopen your session (or export those variables), then run: nexgen sync"))
    return 0


def cmd_down(args) -> int:
    from nexgen_core.stack import runner

    try:
        actions = runner.down(resolve_engine_root(), args.services or None)
    except runner.StackError as exc:
        print(f"{exc}")
        return 1
    for action in actions:
        print(f"  ✓ {action}")
    return 0


def cmd_status(args) -> int:
    from nexgen_core.stack import runner

    rows = runner.status(resolve_engine_root())
    down_count = 0
    for name, alive, where in rows:
        label = t("running") if alive else t("not running")
        print(f"  {label:<10}  {name:<14} {where}")
        if not alive:
            down_count += 1
    if down_count:
        print("\n" + t(
            "{down} of {total} services are not answering. Start them with: nexgen stack up",
            down=down_count, total=len(rows),
        ))
    return 1 if down_count == len(rows) else 0


def cmd_logs(args) -> int:
    from nexgen_core.stack import runner

    try:
        print(runner.logs(resolve_engine_root(), args.service, args.lines))
    except runner.StackError as exc:
        print(f"{exc}")
        return 1
    return 0
