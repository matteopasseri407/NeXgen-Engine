"""The verbs that act on memory.

Memory is the Vault, so they stay under the name `vault`: `nexgen vault
push`, `nexgen vault map`. The invariant tying the name to what the command
acts on survives as a namespace, without a second binary.
"""
from __future__ import annotations

from nexgen_core.i18n import t


def _all(args) -> list[str]:
    """The arguments the dispatcher didn't recognize, in their original order."""
    return list(getattr(args, "passthrough", []) or [])


def register(sub) -> None:
    p = sub.add_parser("vault", help=t("Commands on memory (the Vault)"))
    vsub = p.add_subparsers(dest="vault_command", metavar="verb")

    q = vsub.add_parser("push", aliases=["publish"], help=t("Publish the durable work"))
    q.add_argument("-m", "--message", default="update: vault sync", help=t("Commit message"))
    q.add_argument("files", nargs="*", help=t("Specific files to publish"))
    q.set_defaults(func=cmd_push)

    q = vsub.add_parser("groom", help=t("Consolidate notes: preview by default, apply with confirmation"))
    q.set_defaults(func=cmd_groom)

    q = vsub.add_parser("map", help=t("Map the structure of the notes"))
    q.set_defaults(func=cmd_map)

    q = vsub.add_parser("lifecycle", help=t("Analyze notes' freshness and lifecycle"))
    q.set_defaults(func=cmd_lifecycle)

    p.set_defaults(func=lambda args: _usage(p))

    # `publish` also stays top-level: it's the verb the previous release
    # exposed, and already-installed machines invoke it that way.
    q = sub.add_parser("publish", help=t("Publish the durable work (alias for 'vault push')"))
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
