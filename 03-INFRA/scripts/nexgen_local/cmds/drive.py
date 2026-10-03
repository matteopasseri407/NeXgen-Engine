"""Domain commands: drive stage/confirm/server. Owned here, re-exported by nexgen_local.cli."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from . import base as _base

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

from ..tools import ToolError



def cmd_drive_propose(args: argparse.Namespace) -> int:
    from ..drive_mcp import DriveGateError, stage_upload

    cfg = _config(args)
    try:
        staged = stage_upload(cfg, args.file, args.name or "", args.folder or "")
    except (DriveGateError, ToolError) as exc:
        print(f"nexgen-local: caricamento rifiutato: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(staged, ensure_ascii=False, indent=2))
    else:
        print(
            f"pronto: {staged['name']} ({staged['size']} byte, {staged['mime']})\n"
            f"  locale: {staged['local_path']}\n"
            f"  Carica con: nexgen-local drive-upload {staged['id']} --yes"
        )
    return 0


def cmd_drive_upload(args: argparse.Namespace) -> int:
    from ..drive_mcp import DriveGateError, confirm_upload

    cfg = _config(args)
    try:
        result = confirm_upload(cfg, args.proposal_id, args.yes)
    except (DriveGateError, ToolError) as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"caricato: {result['name']} (proposta {result['id']}, drive id {result['drive_id']})")
    return 0


def cmd_drive_mcp(args: argparse.Namespace) -> int:
    from ..drive_mcp import run_server

    return run_server(_config(args))
