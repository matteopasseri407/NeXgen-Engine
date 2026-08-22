#!/usr/bin/env python3
"""vault-groom — NeXgen Engine v2.

The real command (preview by default, apply guarded behind the safety
gate) lives in `nexgen_core.vault.groom`. This file remains the executable
entry point that `shims.py` points its symlink/launcher at.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.vault.groom import main as groom_main


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    return groom_main(argv)


if __name__ == "__main__":
    sys.exit(main())
