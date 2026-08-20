#!/usr/bin/env python3
"""skills-sync — NeXgen Engine v2 Skill Synchronizer.

Delega interamente a nexgen_core.skills.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.skills import main

if __name__ == "__main__":
    sys.exit(main())
