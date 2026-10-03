"""Domain commands: mail propose/list/send. Owned here, re-exported by nexgen_local.cli."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from . import base as _base
from ..tools import ToolError


_config = _base.command_config
_llm = _base.command_llm


def cmd_mail_propose(args: argparse.Namespace) -> int:
    from ..compose import MailError, format_gate, propose_mail
    from ..llm import LLMError

    cfg = _config(args)
    try:
        llm = _llm(cfg)
        proposal = propose_mail(
            llm,
            cfg,
            args.instruction,
            reply_to=args.reply or "",
            to=args.to or "",
            subject=args.subject or "",
            provider=args.provider or "gmail",
        )
    except LLMError as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 2
    except MailError as exc:
        print(f"nexgen-local: bozza rifiutata: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(asdict(proposal), ensure_ascii=False, indent=2))
    else:
        print(format_gate(proposal))
    return 0


def cmd_mails(args: argparse.Namespace) -> int:
    from ..compose import list_proposals as list_mail_proposals

    cfg = _config(args)
    items = list_mail_proposals(cfg)
    if args.json:
        print(json.dumps([asdict(item) for item in items], ensure_ascii=False, indent=2))
        return 0
    if not items:
        print("nessuna bozza")
        return 0
    for item in items:
        state = "inviata" if item.applied_at else "da approvare"
        print(f"  {item.id}  {state:<15} {item.to}  {item.subject}")
    return 0


def cmd_mail_send(args: argparse.Namespace) -> int:
    from ..compose import MailError, apply_mail

    cfg = _config(args)
    try:
        result = apply_mail(cfg, args.proposal_id, yes=args.yes)
    except (MailError, ToolError) as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"inviata a {result['to']} (proposta {result['id']}, id {result['sent_id']})")
    return 0
