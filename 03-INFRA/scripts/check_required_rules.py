#!/usr/bin/env python3
"""check_required_rules.py — NeXgen Engine v2.

Delega interamente a nexgen_core.tools.required_rules (la logica è stata
portata dentro il package; questo file resta per i riferimenti di CI).
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
