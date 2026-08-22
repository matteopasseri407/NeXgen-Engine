#!/usr/bin/env python3
"""mcp/render.py — NeXgen Engine v2 thin wrapper.

The full logic (write/revert/reset/adopt/inventory) now lives in
nexgen_core.renderer_cli, a single cross-platform implementation. This
file remains for references from INIT.md, README, and the skills, and
delegates without duplicating anything.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.renderer_cli import main

if __name__ == "__main__":
    sys.exit(main())
