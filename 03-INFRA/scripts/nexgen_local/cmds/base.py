"""Shared CLI helpers: config, LLM, result rendering. Single owner."""
from __future__ import annotations

import argparse
import json
import sys

from ..config import LaneConfig
from ..engine import LaneResult


def version_str() -> str:
    from ..version import engine_version

    return engine_version()


def get_config(args: argparse.Namespace) -> LaneConfig:
    return LaneConfig.from_env(
        vault=getattr(args, "vault", None),
        repos=tuple(getattr(args, "repo", None) or []) or None,
        model=getattr(args, "model", None),
        audit=getattr(args, "audit", None),
        router_model=getattr(args, "router_model", None),
        answer_model=getattr(args, "answer_model", None),
    )


def make_llm(cfg: LaneConfig):
    from ..llm import ChatOllamaLLM

    return ChatOllamaLLM(cfg)


def result_payload(result: LaneResult) -> dict:
    return {
        "task": result.task,
        "route": result.route,
        "answer": result.answer,
        "receipts": result.receipts,
        "injection": result.injection,
        "confabulation": result.confabulation,
        "problems": result.problems,
    }


def warn_unverified(problems: list[str]) -> None:
    if not problems:
        return
    print("nexgen-local: ATTENZIONE, risposta non verificata:", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)


def print_receipts(receipts: list[dict]) -> None:
    print("\nRicevute:")
    if receipts:
        for receipt in receipts:
            detail = ", ".join(f"{k}={v}" for k, v in receipt["args"].items())
            print(f"  {receipt['tool']}({detail}) ok={receipt['ok']}")
    else:
        print("  nessuna")
