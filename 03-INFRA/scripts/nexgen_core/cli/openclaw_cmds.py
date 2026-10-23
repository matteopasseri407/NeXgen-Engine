"""`nexgen openclaw`: the harness with the interface.

Unit B1 exposes the two read-only verbs. `plan` says what the engine would
configure and what stays the person's; `doctor` proves a live configuration is
one OpenClaw can actually use. Neither writes, installs, or starts anything.

`setup` and `restart` belong to B2 and register on this same parser: they act on
the same machine and must agree about the same configuration, so the place that
declares the verbs is also the place that will declare theirs.
"""
from __future__ import annotations

import json

from nexgen_core.i18n import t


def _usage(parser) -> int:
    parser.print_help()
    return 0


def _register(sub) -> None:
    p = sub.add_parser("openclaw", help=t("OpenClaw, the harness with the interface (plan and doctor)"))
    psub = p.add_subparsers(dest="openclaw_command", metavar="verb")

    q = psub.add_parser("plan", help=t("Read-only: what the engine would configure, and what stays yours"))
    q.add_argument("--json", action="store_true", help=t("Output in JSON format"))
    q.add_argument("--lan", action="store_true",
                   help=t("Also reachable from the home network (the phone app)"))
    q.add_argument("--acpx", action="store_true", help=t("Allow the acpx plugin too"))
    q.set_defaults(func=cmd_plan)

    q = psub.add_parser("doctor", help=t("Check OpenClaw's installation and configuration"))
    q.add_argument("--json", action="store_true", help=t("Output in JSON format"))
    q.set_defaults(func=cmd_doctor)

    p.set_defaults(func=lambda args: _usage(p))


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


def cmd_doctor(args) -> int:
    from nexgen_core.openclaw.doctor import collect, exit_code, render

    outcomes = collect()
    if getattr(args, "json", False):
        print(json.dumps([outcome.to_dict() for outcome in outcomes], indent=2, ensure_ascii=False))
    else:
        print(render(outcomes))
    return exit_code(outcomes)
