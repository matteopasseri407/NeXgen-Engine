"""Domain commands: relay to external CLIs. Owned here, re-exported by nexgen_local.cli."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from . import base as _base
from ..relay import RELAY_CLIS, RelayError, available_clis, run_relay

def _config(args):
    """Resolve via cli facade when patched in tests, else base owner."""
    try:
        from .. import cli as _cli

        func = getattr(_cli, "_config", None)
        if func is not None and func.__module__ != __name__:
            return func(args)
    except ImportError:
        pass
    return _base.get_config(args)


def _llm(cfg):
    try:
        from .. import cli as _cli

        func = getattr(_cli, "_llm", None)
        if func is not None and func.__module__ != __name__:
            return func(cfg)
    except ImportError:
        pass
    return _base.make_llm(cfg)


_result_payload = _base.result_payload
_warn_unverified = _base.warn_unverified
_print_receipts = _base.print_receipts




def cmd_relay(args: argparse.Namespace) -> int:
    cfg = _config(args)
    if args.list:
        clis = available_clis()
        if args.json:
            print(json.dumps({"supported": list(RELAY_CLIS), "available": clis}, ensure_ascii=False))
        else:
            print("relay — CLI supportati in v0: " + ", ".join(RELAY_CLIS))
            print("installati qui: " + (", ".join(clis) if clis else "nessuno"))
        return 0
    if not args.cli or not args.model or not args.prompt:
        print("nexgen-local: servono --cli, --model e --prompt (oppure --list)", file=sys.stderr)
        return 2
    if args.allow_outside_attach:
        print(
            "nexgen-local: ATTENZIONE allegato fuori dalle radici consentite, forzato su richiesta",
            file=sys.stderr,
        )
    try:
        result = run_relay(
            cfg,
            args.cli,
            args.model,
            args.prompt,
            attach=args.file or None,
            timeout=args.timeout,
            allow_outside_attach=args.allow_outside_attach,
        )
    except RelayError as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print(result.answer or "(nessuna risposta)")
        tail = "troncato" if result.truncated else "completo"
        print(f"\n[{result.cli} · {result.model} · {result.elapsed_s}s · {tail}]")
    return 0
