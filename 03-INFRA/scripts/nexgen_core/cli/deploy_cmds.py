"""The deploy verb: rebuild a stack on the machine that runs it.

One job: offer `nexgen deploy vault-mcp` on the server role, and refuse it
everywhere else with the command that belongs there instead. The work itself is
`nexgen_core.deploy.vault_mcp`; this module only decides who may ask.
"""
from __future__ import annotations

from pathlib import Path

from nexgen_core.i18n import t
from nexgen_core.paths import resolve_engine_root, resolve_state_dir, resolve_vault_data


def register(sub) -> None:
    p = sub.add_parser("deploy", help=t("Rebuild the stack this machine runs from the engine it just updated"))
    ssub = p.add_subparsers(dest="deploy_command", metavar="stack")

    q = ssub.add_parser("vault-mcp", help=t("Rebuild the vault write door, probe it, and roll it back if it fails"))
    q.add_argument(
        "--source", metavar="DIR", default=None,
        help=t("The directory the container is built from, when it is not this engine's own deploy/vault-mcp"),
    )
    q.set_defaults(func=cmd_vault_mcp)

    p.set_defaults(func=lambda a: _usage(p))


def _usage(parser) -> int:
    parser.print_help()
    return 0


def cmd_vault_mcp(args) -> int:
    """Deploy one service, or say why this is not the machine for it."""
    from nexgen_core import roles
    from nexgen_core.deploy import vault_mcp

    engine_root = resolve_engine_root()
    role = roles.declared_role(vault_root=resolve_vault_data())
    if role != roles.SERVER_ROLE:
        print(t(
            "'nexgen deploy' rebuilds the stack on the server that runs it; this machine is {role}.",
            role=role or t("not declared"),
        ))
        print(t("On a machine that runs the connectors itself, use 'nexgen stack up': it does not need a rebuild."))
        return 2
    try:
        result = vault_mcp.deploy(
            engine_root,
            source=None if args.source is None else Path(args.source),
            state_dir=resolve_state_dir(),
        )
    except vault_mcp.DeployError as exc:
        print(f"{exc}")
        return 1
    print(t("{service} is on engine commit {commit} (previous source kept in {backup})",
            service=vault_mcp.SERVICE, commit=result.commit, backup=result.backup))
    return 0
