"""I verbi dello stack locale: avvia, ferma, guarda com'è messo.

L'installazione completa non è più solo "prendi un server": questi cinque
connettori possono girare sulla macchina di chi li usa, e questi comandi sono
il modo di ottenerli senza sapere cosa sia un tunnel.
"""
from __future__ import annotations

from nexgen_core.paths import resolve_engine_root


def register(sub) -> None:
    p = sub.add_parser("stack", help="I servizi dei connettori, su questa macchina")
    ssub = p.add_subparsers(dest="stack_command", metavar="verbo")

    q = ssub.add_parser("up", help="Avvia i servizi e configura i connettori")
    q.add_argument("services", nargs="*", help="Solo alcuni servizi, invece di tutti")
    q.set_defaults(func=cmd_up)

    q = ssub.add_parser("down", help="Ferma i servizi, lasciando intatti i dati")
    q.add_argument("services", nargs="*")
    q.set_defaults(func=cmd_down)

    q = ssub.add_parser("status", help="Quali servizi stanno rispondendo")
    q.set_defaults(func=cmd_status)

    q = ssub.add_parser("logs", help="Le ultime righe di un servizio che non parte")
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

    # I connettori si montano da soli solo se la workstation sa dove trovarli.
    env_values = secrets.read_env_file(env_file(engine_root))
    exports: dict[str, str] = {}
    for service in SERVICES:
        if args.services and service.name not in args.services:
            continue
        exports.update(secrets.resolve_exports(service.exports, service.port, env_values))

    target = secrets.workstation_env_path()
    secrets.write_workstation_env(target, exports)
    print(f"  ✓ Variabili dei connettori scritte in {target}")
    print("\nRiapri la sessione (o esporta quelle variabili), poi esegui: nexgen sync")
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
        print(f"  {'attivo    ' if alive else 'non attivo'}  {name:<14} {where}")
        if not alive:
            down_count += 1
    if down_count:
        print(f"\n{down_count} servizi su {len(rows)} non rispondono. Avviali con: nexgen stack up")
    return 1 if down_count == len(rows) else 0


def cmd_logs(args) -> int:
    from nexgen_core.stack import runner

    try:
        print(runner.logs(resolve_engine_root(), args.service, args.lines))
    except runner.StackError as exc:
        print(f"{exc}")
        return 1
    return 0
