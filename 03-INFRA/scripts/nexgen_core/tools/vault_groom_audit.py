#!/usr/bin/env python3
"""vault_groom_audit — NeXgen Engine v2.

The logic (the promotion gate into the real vault) now lives in
`nexgen_core.vault.audit` (pure audit) and `nexgen_core.vault.audit_cli`
(the command-line entry point). This file remains a bridge for whoever
still invokes it under this name -- `03-INFRA/scripts/vault_groom_audit.py`
at the top of the repo, the vault playbook -- without duplicating the logic.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.vault.audit_cli import main

if __name__ == "__main__":
    sys.exit(main())
