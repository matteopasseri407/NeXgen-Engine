"""The `sup resume` verb: walk a task back to where it stopped and carry on from there.

One job: print the steps the graph replayed, the step it stopped on and why, so a
person sees what was already closed and what is waiting for them. The graph does
the work; this file only reads its answer.
"""
from __future__ import annotations

import argparse
import sys

from .. import supervision_graph as graph


def register(sup_sub) -> None:
    """The resume verb: continue the saved run, or open the first one."""
    verb = sup_sub.add_parser(
        "resume", help="riprende il compito dal passo salvato, senza rifare i passi chiusi",
    )
    verb.add_argument("id", help="id del compito")
    verb.add_argument("--unita", default="", help="unità del giro (senza: quella salvata, o la prima non pubblicata)")
    verb.add_argument("--repo", default="", help="checkout del proprietario su cui applicare i commit approvati")
    verb.add_argument("--branch", default="", help="ramo di quel checkout")
    verb.add_argument("--remote", default="origin", help="remoto da interrogare e su cui spingere")
    verb.add_argument("--push", action="store_true", help="spinge il ramo in avanti dopo il cherry-pick")
    verb.add_argument("--push-with", metavar="COMANDO", default="", help="spinge con il comando dichiarato")
    verb.add_argument("--riparti", action="store_true", help="butta il giro salvato e ricomincia da capo")
    verb.set_defaults(func=cmd_sup_resume)


def cmd_sup_resume(args: argparse.Namespace) -> int:
    """Print what the graph replayed and where it stopped."""
    try:
        result = graph.resume(
            args.id, unit=args.unita, repo=args.repo, branch=args.branch, remote=args.remote,
            push=args.push, push_with=args.push_with, restart=args.riparti,
        )
    except graph.GraphError as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 1
    print(f"compito {result['task']} · unità {result['unit']}")
    for row in result["trace"]:
        print(f"{row['step']}: {row['line']}")
    print(f"stato: {result['state']}")
    print(f"checkpoint {result['checkpoint']}")
    return 0 if result["stop_reason"] != "failed" else 1
