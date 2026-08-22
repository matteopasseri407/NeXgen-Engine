#!/usr/bin/env python3
"""vault-map — NeXgen Engine v2.

Delegates entirely to nexgen_core.tools.vault_map (a port of the logic
from the release's vault-map.py, without duplication).
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.tools.vault_map import main

if __name__ == "__main__":
    sys.exit(main())
