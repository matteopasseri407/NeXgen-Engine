"""Domain commands: run/research/close/explore/eval. Owned here, re-exported by nexgen_local.cli."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from . import base as _base
from ..tools import ToolRegistry
import tempfile

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






def cmd_run(args: argparse.Namespace) -> int:
    from ..graph import run_graph
    from ..llm import LLMError

    cfg = _config(args)
    try:
        llm = _llm(cfg)
    except LLMError as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 2
    tools = ToolRegistry(cfg)

    try:
        from ..jobs import detect_job

        job = detect_job(args.question)
        if job == "research":
            return _cmd_run_research(args, llm, tools, cfg)
        if job == "close":
            return _cmd_run_close(args, llm, tools, cfg)
        result = run_graph(llm, tools, cfg, args.question)
    except LLMError as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(_result_payload(result), ensure_ascii=False, indent=2))
        return 1 if result.problems else 0
    print(result.answer or "(nessuna risposta)")
    _print_receipts(result.receipts)
    _warn_unverified(result.problems)
    return 1 if result.problems else 0


def _cmd_run_research(args: argparse.Namespace, llm, tools, cfg) -> int:
    from ..jobs import job_research

    print("[lane] mestiere: research", file=sys.stderr)
    result = job_research(llm, tools, cfg, args.question)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        return 1 if result.problems else 0
    print(result.answer or "(nessuna risposta)")
    _print_receipts(result.receipts)
    _warn_unverified(result.problems)
    return 1 if result.problems else 0


def _cmd_run_close(args: argparse.Namespace, llm, tools, cfg) -> int:
    from ..source_selection import PATH_RE, existing_file, pinned_path
    from ..jobs import JobError, job_close

    target = ""
    for match in PATH_RE.findall(args.question):
        found = existing_file(cfg, match)
        if found:
            target = pinned_path(found[2], found[1])
            break
    if not target:
        print(
            "nexgen-local: per chiudere una sessione nomina il file (es. 'chiudi la sessione di 04-NOW/note.md')",
            file=sys.stderr,
        )
        return 2
    print("[lane] mestiere: close", file=sys.stderr)
    try:
        result = job_close(llm, tools, cfg, target)
    except JobError as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        return 1 if result.problems else 0
    print(result.answer or "(nessuna bozza)")
    if result.draft_path:
        print(f"\nbozza salvata: {result.draft_path}")
    _print_receipts(result.receipts)
    _warn_unverified(result.problems)
    return 1 if result.problems else 0


def cmd_research(args: argparse.Namespace) -> int:
    from ..jobs import JobError, job_research
    from ..llm import LLMError

    cfg = _config(args)
    try:
        llm = _llm(cfg)
        result = job_research(llm, ToolRegistry(cfg), cfg, args.topic)
    except LLMError as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 2
    except JobError as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        return 1 if result.problems else 0
    print(result.answer or "(nessuna risposta)")
    _print_receipts(result.receipts)
    _warn_unverified(result.problems)
    return 1 if result.problems else 0


def cmd_close(args: argparse.Namespace) -> int:
    from ..jobs import JobError, job_close
    from ..llm import LLMError

    cfg = _config(args)
    try:
        llm = _llm(cfg)
        result = job_close(llm, ToolRegistry(cfg), cfg, args.file, save=args.save)
    except LLMError as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 2
    except JobError as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        return 1 if result.problems else 0
    print(result.answer or "(nessuna bozza)")
    if result.draft_path:
        print(f"\nbozza salvata: {result.draft_path}")
    _print_receipts(result.receipts)
    _warn_unverified(result.problems)
    return 1 if result.problems else 0


def cmd_explore(args: argparse.Namespace) -> int:
    from ..llm import LLMError
    from ..research_graph import ResearchError, research_task
    from ..steps import run_steps

    cfg = _config(args)
    session_id = str(getattr(args, "session_id", "") or "")
    if session_id == "new":
        session_id = ""
        start_persistent = True
    else:
        start_persistent = False
    try:
        llm = _llm(cfg)
        if session_id or start_persistent:
            summary = research_task(llm, cfg, args.task, session_id=session_id, max_steps=args.max_steps)
            print(summary["answer"] or "(nessuna risposta: passaggio a un agente piu' capace)")
            print(f"[sessione: {summary['session_id']} — stato: {summary['status']}]")
            _warn_unverified(summary.get("problems", []))
            if summary.get("problems"):
                return 1
            return 0 if summary["status"] == "answer" else 1
        result = run_steps(llm, ToolRegistry(cfg), cfg, args.task, max_steps=args.max_steps)
    except (LLMError, ResearchError) as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        if result.problems:
            return 1
        return 2 if result.escalated and not result.answer else 0
    if result.answer:
        print(result.answer)
    else:
        print("(nessuna risposta: passaggio a un agente piu' capace)")
    if result.mail_draft:
        print(f"[bozza mail da approvare: {result.mail_draft}]")
    if result.upload_proposal:
        print(f"[proposta upload da approvare: {result.upload_proposal}]")
    _print_receipts(result.receipts)
    for decision in result.decisions:
        mark = "ok" if decision.ok else "KO"
        detail = f" — {decision.detail}" if decision.detail else ""
        print(f"[lane] passo {decision.step}: {decision.action} {decision.arg} [{mark}]{detail}", file=sys.stderr)
    if result.escalated:
        print("[lane] loop chiuso in escalation", file=sys.stderr)
    _warn_unverified(result.problems)
    if result.problems:
        return 1
    return 2 if result.escalated and not result.answer else 0


def cmd_eval(args: argparse.Namespace) -> int:
    from ..evals import SUITES, format_report, run_suite, suite_failed
    from ..llm import LLMError

    cfg = _config(args)
    try:
        llm = _llm(cfg)
    except LLMError as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 2
    suites = list(SUITES) if args.suite == "all" else [args.suite]
    reports = []
    failed = False
    with tempfile.TemporaryDirectory(prefix="nexgen-local-eval-") as tmp:
        for suite in suites:
            report = run_suite(llm, cfg.model, suite, Path(tmp))
            reports.append(report)
            failed = suite_failed(report) or failed
    if args.json:
        print(json.dumps(reports, ensure_ascii=False, indent=2))
    else:
        for report in reports:
            print(format_report(report))
    return 1 if failed else 0
