#!/usr/bin/env python3
"""firecrawl-search-health.py — NeXgen Engine v2.

Delegates entirely to nexgen_core.tools.firecrawl_health (the logic was
moved into the package; this file remains for external references).
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.tools.firecrawl_health import main

if __name__ == "__main__":
    raise SystemExit(main())
