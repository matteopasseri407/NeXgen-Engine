"""Domain commands: calendar propose/list/apply. Owned here, re-exported by nexgen_local.cli."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from . import base as _base
from ..proposals import proposal_status
from ..tools import ToolError


_config = _base.command_config


def cmd_cal_propose(args: argparse.Namespace) -> int:
    from ..calendars import CalendarError, format_gate, propose_delete, propose_event

    cfg = _config(args)
    try:
        if args.delete:
            proposal = propose_delete(cfg, args.calendar or "primary", args.delete)
        else:
            proposal = propose_event(
                cfg, args.summary or "", args.start or "", args.end or "",
                args.description or "", args.location or "", args.calendar or "primary",
            )
    except CalendarError as exc:
        print(f"nexgen-local: proposta rifiutata: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(asdict(proposal), ensure_ascii=False, indent=2))
    else:
        print(format_gate(proposal))
    return 0


def cmd_cals(args: argparse.Namespace) -> int:
    from ..calendars import list_proposals as list_cal_proposals

    cfg = _config(args)
    items = list_cal_proposals(cfg)
    if args.json:
        print(json.dumps([asdict(item) for item in items], ensure_ascii=False, indent=2))
        return 0
    if not items:
        print("nessuna proposta")
        return 0
    for item in items:
        state = proposal_status(item, "applicata")
        print(f"  {item.id}  {state:<15} {item.kind}  {item.summary or item.event_id}")
    return 0


def cmd_cal_apply(args: argparse.Namespace) -> int:
    from ..calendars import CalendarError, apply_proposal as apply_cal

    cfg = _config(args)
    try:
        result = apply_cal(cfg, args.proposal_id, yes=args.yes)
    except (CalendarError, ToolError) as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"applicata: {result['kind']} (proposta {result['id']}, id {result['done_id']})")
    return 0
