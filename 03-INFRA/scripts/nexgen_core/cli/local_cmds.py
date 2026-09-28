"""The `local` verb group: the optional governed lane for local models.

The lane itself lives in ``nexgen_local`` and is only installed with the
``[local]`` extra. This group exists so a cloned checkout reaches the lane
through the same name tree as everything else: ``nexgen local ...`` and the
``nexgen-local`` shim both land here, and a machine without the extra gets
one clear sentence instead of an ImportError traceback.
"""
from __future__ import annotations

import argparse
import sys

from nexgen_core.i18n import t


def dispatch(lane_args: list[str]) -> int:
    """Hand the arguments to the lane's own CLI, unchanged."""
    try:
        from nexgen_local.cli import main as lane_main
    except ImportError:
        print(
            t("The local lane needs the optional extra: pip install 'nexgen-engine[local]'"),
            file=sys.stderr,
        )
        return 2
    return lane_main(list(lane_args))


def _cmd_local(args: argparse.Namespace) -> int:
    return dispatch(list(getattr(args, "lane_args", None) or []))


def register(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(
        "local",
        help=t("Governed local-model lane (read-only, optional)"),
        add_help=False,
    )
    parser.add_argument("lane_args", nargs=argparse.REMAINDER)
    parser.set_defaults(func=_cmd_local)
