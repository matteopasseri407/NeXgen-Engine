"""Domain commands: run/eval. Owned here, re-exported by nexgen_local.cli."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from . import base as _base
from ..tools import ToolRegistry


_config = _base.command_config
_llm = _base.command_llm
_result_payload = _base.result_payload
_warn_unverified = _base.warn_unverified
_print_receipts = _base.print_receipts


def cmd_run(args: argparse.Namespace) -> int:
    from ..engine import run_lane
    from ..llm import LLMError

    cfg = _config(args)
    try:
        llm = _llm(cfg)
    except LLMError as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 2
    tools = ToolRegistry(cfg)

    try:
        result = run_lane(llm, tools, cfg, args.question)
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


def _run_eval_cmd(llm, cfg, args: argparse.Namespace) -> int:
    from ..evals import SUITES, format_report, run_suite, suite_failed

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


def cmd_eval(args: argparse.Namespace) -> int:
    from ..llm import LLMError
    from ..source_selection import INJECTION_LAYER_ENV

    if getattr(args, "bare", False):
        os.environ[INJECTION_LAYER_ENV] = "0"
    cfg = _config(args)
    try:
        llm = _llm(cfg)
    except LLMError as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 2
    return _run_eval_cmd(llm, cfg, args)
