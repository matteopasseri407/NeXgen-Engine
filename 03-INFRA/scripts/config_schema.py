"""Ponte di compatibilità verso `nexgen_core.config`.

Esiste perché il Council importa questo nome per percorso. Il rewrite ha
rinominato `ConfigValidationError` in `ConfigError`, e siccome questo ponte
faceva `import *` il nome vecchio è semplicemente sparito: il comando
`council` andava in ImportError prima ancora di stampare l'aiuto.

Rinominare un simbolo pubblico è un cambio di contratto, e un cambio di
contratto ha bisogno di una finestra. Questa è la finestra.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.config import (
    ConfigError,
    load_council_config,
    load_mcp_manifest,
    load_skills_manifest,
)

#: Il nome che la versione precedente esponeva.
ConfigValidationError = ConfigError

__all__ = [
    "ConfigError",
    "ConfigValidationError",
    "load_council_config",
    "load_mcp_manifest",
    "load_skills_manifest",
]
