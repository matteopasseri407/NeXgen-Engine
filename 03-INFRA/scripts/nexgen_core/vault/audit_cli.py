#!/usr/bin/env python3
"""Standalone CLI for `nexgen_core.vault.audit` -- for compatibility.

`groom.py` calls `run_audit()` directly in-process during `apply`; but
`vault_groom_audit.py` (in tools/ and as the top-level bridge in scripts/)
is documented in the vault playbook as something that can also be invoked
from the command line with the same historical arguments, to inspect a run
by hand. This module is that entry point.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from nexgen_core.i18n import t
from nexgen_core.vault.audit import AuditRequest, run_audit


def _build_request(args: argparse.Namespace) -> AuditRequest:
    return AuditRequest(
        vault=Path(args.vault),
        clone=Path(args.clone),
        branch=args.branch,
        base=args.base,
        archive_root=args.archive_root,
        state_dir=Path(args.state_dir),
        timestamp=args.timestamp,
        runner=args.runner,
        model=args.model,
        tranche_sha256=args.tranche_sha256,
        plan_record=Path(args.plan_record),
        propose_log=Path(args.propose_log),
        write_log=Path(args.write_log),
        write_exit_code=args.write_exit_code,
        push_if_clean=args.push_if_clean,
        engine_scripts=Path(args.engine_scripts) if args.engine_scripts else None,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", required=True, help=t("the REAL vault -- never touched until promotion"))
    parser.add_argument("--clone", required=True, help=t("the temp-clone gate's working dir"))
    parser.add_argument("--branch", default="main")
    parser.add_argument("--base", required=True, help=t("the real vault's HEAD before the clone was made"))
    parser.add_argument("--archive-root", default="99-ARCHIVE")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--timestamp", required=True)
    parser.add_argument("--runner", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--tranche-sha256", required=True)
    parser.add_argument("--plan-record", required=True)
    parser.add_argument("--propose-log", required=True)
    parser.add_argument("--write-log", required=True)
    parser.add_argument(
        "--write-exit-code", required=True, type=int,
        help=t("exit code of the write-pass runner CLI -- non-zero blocks promotion unconditionally"),
    )
    parser.add_argument(
        "--push-if-clean", action="store_true", default=False,
        help=t("attempt agent_sync.py publish after a successful promotion; omit to never push this run"),
    )
    parser.add_argument(
        "--engine-scripts", default=None,
        help=t("directory containing agent_sync.py (required together with --push-if-clean)"),
    )
    args = parser.parse_args(argv)

    if args.push_if_clean and not args.engine_scripts:
        parser.error("--push-if-clean requires --engine-scripts")

    request = _build_request(args)
    record, exit_code = run_audit(request)

    state_dir = Path(args.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    record_path = state_dir / f"{args.timestamp}.json"
    record_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(t("audit record: {record_path}", record_path=record_path))

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
