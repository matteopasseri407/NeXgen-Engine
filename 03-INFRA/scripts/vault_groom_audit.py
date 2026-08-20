#!/usr/bin/env python3
"""vault_groom_audit — NeXgen Engine v2.

Delega interamente a nexgen_core.tools.vault_groom_audit (la logica è stata
portata dentro il package, questo file resta per compatibilità dei riferimenti
esterni come conftest e playbook).
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
