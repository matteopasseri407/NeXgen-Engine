"""I nomi ereditati, come punti di ingresso del pacchetto installato.

Quando il motore si installa come pacchetto Python, gli alias non possono
essere script generati: devono essere funzioni che il packaging registra.
Sono le stesse di `shims.py`, con la stessa tabella, perché una seconda
tabella è una seconda cosa che va in deriva.
"""
from __future__ import annotations

import sys
from collections.abc import Callable

from nexgen_core.cli import main as nexgen_main
from nexgen_core.shims import LEGACY_ALIASES


def _alias(name: str) -> Callable[[], int]:
    """Costruisce l'entrypoint di un nome ereditato."""
    prefix = LEGACY_ALIASES[name]

    def run() -> int:
        return nexgen_main(prefix + sys.argv[1:])

    run.__name__ = name.replace("-", "_")
    run.__doc__ = f"'{name}': nome storico di 'nexgen {' '.join(prefix)}'.".strip()
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
