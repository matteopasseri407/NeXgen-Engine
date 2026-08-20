#!/usr/bin/env python3
"""vault_groom_audit — NeXgen Engine v2.

La logica (il gate di promozione verso il vault reale) ora vive in
`nexgen_core.vault.audit` (audit puro) e `nexgen_core.vault.audit_cli` (il
punto d'ingresso da riga di comando). Questo file resta un ponte per chi lo
invoca ancora con questo nome -- `03-INFRA/scripts/vault_groom_audit.py` in
cima al repo, il playbook del vault -- senza duplicare la logica.
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
