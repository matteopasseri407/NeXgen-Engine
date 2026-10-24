"""Domain commands: run/eval. Owned here, re-exported by nexgen_local.cli."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from . import base as _base


_config = _base.command_config
_llm = _base.command_llm


def cmd_run(args: argparse.Namespace) -> int:
    print(
        "nexgen-local run è stato ritirato: usa 'openclaw agent' o 'openclaw tui' "
        "(l'esecuzione degli agenti appartiene a OpenClaw)."
    )
    return 0


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
