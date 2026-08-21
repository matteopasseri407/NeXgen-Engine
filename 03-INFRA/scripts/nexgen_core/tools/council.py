#!/usr/bin/env python3
"""Launcher for AI Council — NeXgen Engine v2."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from nexgen_core.paths import resolve_engine_root


def main(argv: list[str] | None = None) -> int:
    """Forwards the invocation to the canonical council.py module."""
    if argv is None:
        argv = sys.argv[1:]

    engine_root = resolve_engine_root()
    council_py = engine_root / "agent-universal-layer" / "council" / "council.py"

    if not council_py.is_file():
        here = Path(__file__).resolve().parents[3]
        council_py = here / "agent-universal-layer" / "council" / "council.py"

    if not council_py.is_file():
        print(f"[ERROR] AI Council orchestrator not found at {council_py}", file=sys.stderr)
        return 1

    res = subprocess.run([sys.executable, str(council_py), *argv])
    return res.returncode


if __name__ == "__main__":
    sys.exit(main())
