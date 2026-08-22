"""Compatibility bridge to `nexgen_core.config`.

Exists because the Council imports this name by path. The rewrite renamed
`ConfigValidationError` to `ConfigError`, and since this bridge did
`import *`, the old name simply vanished: the `council` command hit an
ImportError before it even got to printing help.

Renaming a public symbol is a contract change, and a contract change
needs a window. This is that window.
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

#: The name the previous version exposed.
ConfigValidationError = ConfigError

__all__ = [
    "ConfigError",
    "ConfigValidationError",
    "load_council_config",
    "load_mcp_manifest",
    "load_skills_manifest",
]
