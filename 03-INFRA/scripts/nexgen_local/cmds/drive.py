"""Domain commands: drive stage/confirm/server. Owned here, re-exported by nexgen_local.cli."""
from __future__ import annotations

import argparse
import json
import sys

from . import base as _base
from ..tools import ToolError


_config = _base.command_config


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
