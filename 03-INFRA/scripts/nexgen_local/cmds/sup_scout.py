"""The `sup scout` verb: a question handed to the explorer, on a copy that must not change.

One job: take the owner's question, have the round prepared in a fixed trusted
folder, and print where the brief, the copy and the report are. The tab side is
the same verb with `--in-scheda`, so a round interrupted by a closed terminal can
be replayed by hand without preparing a second copy.
"""
from __future__ import annotations

import argparse
import sys

from .. import supervision_scout as scout
from ..supervision_state import SupervisionError


def register(sup_sub) -> None:
    """The scout verb: open one round, or run the round the tab was opened for."""
    verb = sup_sub.add_parser(
        "scout", help="apre il ricognitore su una copia staccata del repository e verifica che resti pulita",
    )
    verb.add_argument("id", help="id del compito")
    verb.add_argument("domanda", nargs="?", default="", help="in una riga, cosa deve guardare il ricognitore")
    verb.add_argument("--in-scheda", action="store_true", help=argparse.SUPPRESS)
    verb.add_argument("--round", type=int, default=0, help=argparse.SUPPRESS)
    verb.set_defaults(func=cmd_sup_scout)


def cmd_sup_scout(args: argparse.Namespace) -> int:
    """Prepare the round and open its tab, or run the explorer inside the tab already open."""
    if args.in_scheda:
        return scout.run_in_tab(args.id, args.round)
    try:
        record = scout.open_round(args.id, args.domanda)
    except SupervisionError as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 1
    print(f"ricognizione {record['round']} · compito {record['task']}")
    print(f"copia {record['folder']} (staccata su {record['base'][:12]})")
    print(f"brief {record['brief']}")
    print(f"rapporto atteso {record['report']}")
    print(f"scheda «{record['window']}»")
    print("fiducia della cartella: " + ("aggiunta ora" if record["trusted"] else "già presente"))
    print("lo scout non modifica il codice: il motore ricontrolla la copia a fine giro")
    return 0
