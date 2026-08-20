#!/usr/bin/env python3
"""vault-groom — NeXgen Engine v2.

Inoltra l'invocazione all'audit del grooming (vault_groom_audit) che ora vive
in nexgen_core, la copia canonica senza duplicazioni.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.tools.vault_groom_audit import main as audit_main


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    return audit_main(argv)


if __name__ == "__main__":
    sys.exit(main())
