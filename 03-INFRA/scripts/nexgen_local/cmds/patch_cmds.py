"""Domain commands: patch propose/list/apply. Owned here, re-exported by nexgen_local.cli."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from . import base as _base
from ..patch import PatchError, apply_proposal, format_gate, list_proposals, propose_patch
from ..proposals import proposal_status
from ..tools import ToolError


_config = _base.command_config
_llm = _base.command_llm


def cmd_propose(args: argparse.Namespace) -> int:
    from ..llm import LLMError

    cfg = _config(args)
    try:
        llm = _llm(cfg)
        proposal = propose_patch(llm, cfg, args.file, args.instruction)
    except LLMError as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 2
    except PatchError as exc:
        print(f"nexgen-local: proposta rifiutata: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(asdict(proposal), ensure_ascii=False, indent=2))
    else:
        print(format_gate(proposal))
    return 0 if proposal.dry_run else 1


def cmd_proposals(args: argparse.Namespace) -> int:
    cfg = _config(args)
    items = list_proposals(cfg)
    if args.json:
        print(json.dumps([asdict(item) for item in items], ensure_ascii=False, indent=2))
        return 0
    if not items:
        print("nessuna proposta")
        return 0
    for item in items:
        state = proposal_status(item, "applicata", "pronta" if item.dry_run else "dry-run fallito")
        print(f"  {item.id}  {state:<15} {item.file}")
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    cfg = _config(args)
    try:
        result = apply_proposal(cfg, args.proposal_id, yes=args.yes, verify=args.verify or None)
    except (PatchError, ToolError) as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"applicata: {result['file']} (proposta {result['id']})")
        if result["verify"]:
            print(result["verify"])
    if result.get("verified") is False:
        print(
            "nexgen-local: verifica fallita: la modifica e' applicata ma non verificata",
            file=sys.stderr,
        )
        return 3
    return 0
