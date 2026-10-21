"""The `sup pulizia` verb: what the engine left behind, and one flag to close it.

One job: ask `supervision_cleanup` what is stale, print every list it found, and
with `--chiudi` close the stale tabs. It decides nothing itself: the survey, the
reason of each tab and the closing rule live in the domain module.
"""
from __future__ import annotations

import argparse
import sys

from .. import supervision_cleanup as cleanup
from ..supervision_state import SupervisionError


def register(sup_sub) -> None:
    """The cleanup verb: list what is stale, or list it and close it."""
    verb = sup_sub.add_parser(
        "pulizia",
        help="elenca (e con --chiudi chiude) le schede e i processi che il motore ha lasciato aperti",
    )
    verb.add_argument("--chiudi", action="store_true", help="chiude le schede dei compiti finiti o spariti")
    verb.set_defaults(func=cmd_sup_pulizia)


def cmd_sup_pulizia(args: argparse.Namespace) -> int:
    """Print the survey, then close the stale tabs when the owner asked for it."""
    try:
        found = cleanup.survey()
    except SupervisionError as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 1
    print("\n".join(cleanup.lines(found)))
    if not args.chiudi:
        return 0
    try:
        result = cleanup.close_tabs(found["close"])
    except SupervisionError as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 1
    print("\n".join(cleanup.closed_lines(result)))
    return 0
