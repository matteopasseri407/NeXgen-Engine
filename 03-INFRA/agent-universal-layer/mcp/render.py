#!/usr/bin/env python3
"""mcp/render.py — NeXgen Engine v2 thin wrapper.

La logica completa (write/revert/reset/adopt/inventory) vive ora in
nexgen_core.renderer_cli, un'unica implementazione cross-platform. Questo
file resta per i riferimenti di INIT.md, README e delle skill, e delega
senza duplicare nulla.
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
