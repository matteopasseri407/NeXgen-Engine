#!/usr/bin/env python3
"""vault_groom_audit — NeXgen Engine v2.

Delegates entirely to nexgen_core.tools.vault_groom_audit (the logic was
moved into the package; this file remains for compatibility with external
references such as conftest and the playbook).
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.tools.vault_groom_audit import main

if __name__ == "__main__":
    sys.exit(main())
