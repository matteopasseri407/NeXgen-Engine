#!/usr/bin/env python3
"""agent_sync — NeXgen Engine v2 Entrypoint.

Forwards all CLI commands and operations to the nexgen_core package.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# Table of commands managed by the layer
LINKED_COMMANDS: dict[str, dict[str, object]] = {
    "agent-sync":        {"source": "engine", "posix": True,  "windows": True},
    "agent-doctor":      {"source": "engine", "posix": True,  "windows": True},
    "agent-chrome":      {"source": "engine", "posix": True,  "windows": True},
    "agent-now":         {"source": "engine", "posix": True,  "windows": True},
    "agent-open-folder": {"source": "engine", "posix": True,  "windows": True},
    "council":           {"source": "engine", "posix": True,  "windows": True},
    "firecrawl-local":   {"source": "engine", "posix": True,  "windows": True},
    "nexgen-update":     {"source": "engine", "posix": True,  "windows": True},
    "skills-sync":       {"source": "engine", "posix": True,  "windows": True},
    "agent-skill":       {"source": "engine", "posix": True,  "windows": True},
    "vault-push":        {"source": "engine", "posix": True,  "windows": True},
    "vault-groom":       {"source": "engine", "posix": True,  "windows": True},
    "vault-map":         {"source": "engine", "posix": True,  "windows": True},
    "vault-ocr-local":   {"source": "vault",  "posix": True,  "windows": False, "optional": True},
}

from nexgen_core.cli import main

if __name__ == "__main__":
    sys.exit(main())
