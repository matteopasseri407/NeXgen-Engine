"""The legacy names, as entry points of the installed package.

When the engine installs as a Python package, the aliases can't be generated
scripts: they have to be functions that packaging registers. They're the
same ones as `shims.py`, with the same table, because a second table is a
second thing that drifts.
"""
from __future__ import annotations

import sys
from collections.abc import Callable

from nexgen_core.cli import main as nexgen_main
from nexgen_core.shims import LEGACY_ALIASES


def _alias(name: str) -> Callable[[], int]:
    """Builds the entry point for a legacy name."""
    prefix = LEGACY_ALIASES[name]

    def run() -> int:
        return nexgen_main(prefix + sys.argv[1:])

    run.__name__ = name.replace("-", "_")
    run.__doc__ = f"'{name}': historical name for 'nexgen {' '.join(prefix)}'.".strip()
    return run


agent_sync = _alias("agent-sync")
agent_doctor = _alias("agent-doctor")
vault_push = _alias("vault-push")
vault_groom = _alias("vault-groom")
vault_map = _alias("vault-map")
agent_now = _alias("agent-now")
agent_open_folder = _alias("agent-open-folder")
agent_chrome = _alias("agent-chrome")
firecrawl_local = _alias("firecrawl-local")
nexgen_update = _alias("nexgen-update")
skills_sync = _alias("skills-sync")
agent_skill = _alias("agent-skill")
council = _alias("council")
