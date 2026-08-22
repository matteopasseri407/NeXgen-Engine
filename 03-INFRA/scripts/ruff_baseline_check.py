#!/usr/bin/env python3
"""ruff_baseline_check.py — NeXgen Engine v2.

Delegates entirely to nexgen_core.tools.ruff_baseline (the logic was
moved into the package; this file remains for CI references).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("NEXGEN_RUFF_ENTRY_DIR", str(Path(__file__).resolve().parent))

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.tools.ruff_baseline import main

if __name__ == "__main__":
    raise SystemExit(main())
