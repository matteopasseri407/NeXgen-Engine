"""The `sup rebase` verb: put the unit back on the tip of its base and re-fingerprint the copy.

One job: print where the base was, where it is now, and what happened to the
unit's commits. The work is `supervision_rebase`; this file only reads the answer
so a person knows whether to collect again.
"""
from __future__ import annotations

import argparse
import sys

from .. import supervision_rebase as rebase_module
from ..supervision_state import SupervisionError


def register(sup_sub) -> None:
    """The rebase verb: realign the unit on its base."""
    verb = sup_sub.add_parser(
        "rebase", help="riallinea il ramo dell'unità alla punta attuale della sua base, senza rete",
    )
    verb.add_argument("id", help="id del compito")
    verb.add_argument("unita", help="nome dell'unità")
    verb.set_defaults(func=cmd_sup_rebase)


def cmd_sup_rebase(args: argparse.Namespace) -> int:
    """Print the two bases and the new head, or say that nothing needed to move."""
    try:
        result = rebase_module.rebase(args.id, args.unita)
    except SupervisionError as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 1
    print(f"unità {result['unit']} · ramo {result['branch']}")
    if not result["changed"]:
        print(result["reason"])
        print(f"base {result['base'][:12]} · head {result['head'][:12]}")
        return 0
    print(f"base {result['previous_base'][:12]} → {result['base'][:12]} ({result['base_ref'] or 'ref'})")
    print(f"head {result['head'][:12]}")
    print(f"configurazione git: impronta rifatta ({len(result['config_hashes'])} file)")
    print("raccogli di nuovo: `sup collect " + f"{args.id} {result['unit']}`")
    return 0
