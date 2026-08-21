#!/usr/bin/env python3
"""Launcher for AI Council — NeXgen Engine v2."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    """Forwards the invocation to the canonical council.py module."""
    if argv is None:
        argv = sys.argv[1:]

    # This file lives in nexgen_core/tools/, so `03-INFRA` is three levels
    # up. The fallback used parents[2] and pointed at a directory that has
    # never existed, so a machine without AGENT_ENGINE_ROOT found nothing.
    here = Path(__file__).resolve().parents[3]
    engine_root = Path(os.environ.get("AGENT_ENGINE_ROOT") or here)
    council_py = engine_root / "agent-universal-layer" / "council" / "council.py"

    if not council_py.is_file():
        council_py = here / "agent-universal-layer" / "council" / "council.py"

    if not council_py.is_file():
        print(f"[ERROR] AI Council orchestrator not found at {council_py}", file=sys.stderr)
        return 1

    res = subprocess.run([sys.executable, str(council_py), *argv])
    return res.returncode


if __name__ == "__main__":
    sys.exit(main())
