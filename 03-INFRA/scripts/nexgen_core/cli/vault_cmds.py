"""I verbi che agiscono sulla memoria.

La memoria è il Vault, quindi restano sotto il nome `vault`: `nexgen vault
push`, `nexgen vault map`. L'invariante che lega il nome a ciò su cui il
comando agisce sopravvive come spazio dei nomi, senza un secondo binario.
"""
from __future__ import annotations


def _all(args) -> list[str]:
    """Gli argomenti che il dispatcher non ha riconosciuto, nel loro ordine."""
    return list(getattr(args, "passthrough", []) or [])


def register(sub) -> None:
    p = sub.add_parser("vault", help="Comandi sulla memoria (il Vault)")
    vsub = p.add_subparsers(dest="vault_command", metavar="verbo")

    q = vsub.add_parser("push", aliases=["publish"], help="Pubblica il lavoro durevole")
    q.add_argument("-m", "--message", default="update: vault sync", help="Messaggio di commit")
    q.add_argument("files", nargs="*", help="File specifici da pubblicare")
    q.set_defaults(func=cmd_push)

    q = vsub.add_parser("groom", help="Consolida le note: anteprima di default, apply con conferma")
    q.set_defaults(func=cmd_groom)

    q = vsub.add_parser("map", help="Mappa la struttura delle note")
    q.set_defaults(func=cmd_map)

    q = vsub.add_parser("lifecycle", help="Analizza freschezza e ciclo di vita delle note")
    q.set_defaults(func=cmd_lifecycle)

    p.set_defaults(func=lambda args: _usage(p))

    # `publish` resta anche di primo livello: è il verbo che la release
    # precedente esponeva, e le macchine già installate lo invocano così.
    q = sub.add_parser("publish", help="Pubblica il lavoro durevole (alias di 'vault push')")
    q.add_argument("-m", "--message", default="update: vault sync")
    q.add_argument("files", nargs="*")
    q.set_defaults(func=cmd_push)


def _usage(parser) -> int:
    parser.print_help()
    return 0


def cmd_push(args) -> int:
    from nexgen_core.publisher import Publisher

    code, msg = Publisher().publish(message=args.message, files=args.files or None)
    print(msg)
    return code


def cmd_groom(args) -> int:
    from nexgen_core.tools.vault_groom import main as groom_main

    return groom_main(_all(args))


def cmd_map(args) -> int:
    from nexgen_core.tools.vault_map import main as map_main

    return map_main(_all(args))


def cmd_lifecycle(args) -> int:
    from nexgen_core.tools.vault_lifecycle_audit import main as lifecycle_main

    return lifecycle_main(_all(args))
