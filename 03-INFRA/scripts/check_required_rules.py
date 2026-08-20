#!/usr/bin/env python3
"""check_required_rules.py — NeXgen Engine v2.

Delegates entirely to nexgen_core.tools.required_rules (the logic was
moved into the package; this file remains for CI references).
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.tools.required_rules import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
