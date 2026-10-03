"""Domain commands: workflow propose/list/run. Owned here, re-exported by nexgen_local.cli."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from . import base as _base
from ..tools import ToolError


_config = _base.command_config


def cmd_wf_propose(args: argparse.Namespace) -> int:
    from ..workflows import WorkflowError, format_gate, propose_run

    cfg = _config(args)
    try:
        params = json.loads(args.params) if args.params else {}
        proposal = propose_run(cfg, args.workflow or "", params)
    except (WorkflowError, ValueError) as exc:
        print(f"nexgen-local: esecuzione rifiutata: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(asdict(proposal), ensure_ascii=False, indent=2))
    else:
        print(format_gate(proposal))
    return 0


def cmd_wfs(args: argparse.Namespace) -> int:
    from ..workflows import WorkflowError, list_proposals as list_wf_proposals, load_allowlist

    cfg = _config(args)
    try:
        allowed = load_allowlist()
    except WorkflowError as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"allowed": sorted(allowed), "proposals": [asdict(p) for p in list_wf_proposals(cfg)]}, ensure_ascii=False, indent=2))
        return 0
    print("consentiti: " + (", ".join(sorted(allowed)) or "(nessuno)"))
    for item in list_wf_proposals(cfg):
        state = "eseguita" if item.applied_at else "da approvare"
        print(f"  {item.id}  {state:<15} {item.workflow}")
    return 0


def cmd_wf_run(args: argparse.Namespace) -> int:
    from ..workflows import WorkflowError, confirm_run

    cfg = _config(args)
    try:
        result = confirm_run(cfg, args.proposal_id, args.yes)
    except (WorkflowError, ToolError) as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"eseguito: {result['id']}\n{result['outcome'] or '(nessun output)'}")
    return 0
