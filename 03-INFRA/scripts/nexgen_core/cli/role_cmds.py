# nexgen: one-job reason="say which role this machine was configured with, and record it when a person asks"
"""The machine's role, as the engine recorded it.

One job: read and write the record that says whether this machine is the
always-on server, a node beside a gateway, or a standalone PC.

The record exists because the rest of the engine cannot ask the installer a
question later: a checkout of the Vault that is only a reader (the gateway's
clone on a server) is decided from the role, and a machine that was prepared by
hand never ran the role that writes it. That is how a server kept committing in
a clone nobody could push from. `nexgen role set server` is the one command that
closes that gap without reinstalling anything.

It never guesses: `nexgen role` prints what is recorded and stops. A machine with
nothing recorded says so, and stays exactly as it was.
"""
from __future__ import annotations

from nexgen_core import roles
from nexgen_core.i18n import t


def cmd_role(args) -> int:
    """Say which role this machine recorded, and where it was read from."""
    recorded = roles.declared_role()
    if recorded:
        print(t("This machine is recorded as: {role}", role=recorded))
        print(t("The record lives in {path}", path=roles.role_record_path()))
        return 0
    print(t("No role is recorded on this machine."))
    print(t("If this is the always-on gateway, record it with 'nexgen role set server'."))
    return 0


def cmd_role_set(args) -> int:
    """Record the role a person names, and say where it went."""
    try:
        recorded = roles.declare_role(args.role)
    except ValueError as exc:
        print(str(exc))
        return 2
    if not recorded:
        print(t("The role could not be recorded in {path}", path=roles.role_record_path()))
        return 1
    print(t("Recorded the role: {role}", role=args.role))
    print(t("The record lives in {path}", path=roles.role_record_path()))
    return 0


def register(sub) -> None:
    p = sub.add_parser("role", help=t("Which role this machine runs, and how to record it"))
    rsub = p.add_subparsers(dest="role_command", metavar="role")
    p.set_defaults(func=cmd_role)

    q = rsub.add_parser("set", help=t("Record the role this machine runs"))
    q.add_argument("role", choices=list(roles.ROLES), help=t("The role to record"))
    q.set_defaults(func=cmd_role_set)
