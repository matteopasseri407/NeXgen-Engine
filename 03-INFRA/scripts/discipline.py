#!/usr/bin/env python3
"""discipline.py -- size ratchet, shell/PowerShell gate and review receipt.

Delegates entirely to nexgen_core.discipline (the logic lives in the package;
this file is the entry point engine docs and CI call).
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.discipline.cli import main  # noqa: E402 - sys.path shim for cloned checkout

if __name__ == "__main__":
    sys.exit(main())
