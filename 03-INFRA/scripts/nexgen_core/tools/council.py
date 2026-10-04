#!/usr/bin/env python3
"""Launcher for AI Council — NeXgen Engine v2 (not the implementation).

The logic lives in 03-INFRA/agent-universal-layer/council/council.py;
this module only subprocesses it so `nexgen council` works installed
or cloned. Do not add logic here: agents citing this file as the
implementation will describe a 34-line forwarder as the orchestrator."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from nexgen_core.i18n import t
from nexgen_core.paths import resolve_engine_root


def main(argv: list[str] | None = None) -> int:
    """Forwards the invocation to agent-universal-layer/council/council.py."""
    if argv is None:
        argv = sys.argv[1:]

    engine_root = resolve_engine_root()
    council_py = engine_root / "agent-universal-layer" / "council" / "council.py"

    if not council_py.is_file():
        here = Path(__file__).resolve().parents[3]
        council_py = here / "agent-universal-layer" / "council" / "council.py"

    if not council_py.is_file():
        print(t("AI Council orchestrator not found at {path}. Reinstall the engine.", path=council_py), file=sys.stderr)
        return 1

    res = subprocess.run([sys.executable, str(council_py), *argv])
    return res.returncode


if __name__ == "__main__":
    sys.exit(main())
