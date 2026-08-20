#!/usr/bin/env python3
"""Il publisher (vault-push): pubblicazione sicura delle modifiche per NeXgen Engine v2.

Contratto:
1. Acquisisce il lock esclusivo host-wide (condiviso con il Guard).
2. Esegue commit e push in un'unica operazione atomica.
3. Se il remoto è avanzato, tenta un rebase pulito; in caso di conflitto si arresta senza distruggere dati.
4. Sincronizza i mirror configurati in modalità best-effort.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.git_ops import (
    get_current_branch,
    publish_changes,
    resolve_remotes,
)
from nexgen_core.lock import HostLock
from nexgen_core.paths import resolve_vault_data


class Publisher:
    """Gestore della pubblicazione per vault-push."""

    def __init__(self, vault_data: Path | None = None) -> None:
        _v = resolve_vault_data(override=vault_data)
        self.vault_data = _v

    def publish(
        self,
        message: str = "update: vault sync",
        files: list[str] | None = None,
        timeout: float = 30.0
    ) -> tuple[int, str]:
        """Esegue il flusso completo di pubblicazione sotto lock."""
        with HostLock(timeout=timeout, command_name="vault-push"):
            auth_remote, mirrors = resolve_remotes(self.vault_data)
            # Stessa precedenza dei controlli: chi forza il branch con la
            # variabile d'ambiente lo forza anche per la pubblicazione.
            branch = (
                os.environ.get("KNOWLEDGE_VAULT_BRANCH")
                or get_current_branch(self.vault_data)
                or "main"
            )

            success, msg = publish_changes(
                repo_dir=self.vault_data,
                branch=branch,
                remote=auth_remote,
                mirrors=mirrors,
                commit_msg=message,
                files_to_commit=files,
            )

            if success:
                return 0, msg
            return 1, msg


def main(argv: list[str] | None = None) -> int:
    """Entry point CLI per il comando vault-push (v2)."""
    parser = argparse.ArgumentParser(
        prog="vault-push",
        description="Pubblica i commit del Vault verso il remoto autoritativo",
    )
    parser.add_argument("-m", "--message", default="update: vault sync", help="Messaggio di commit")
    parser.add_argument("files", nargs="*", help="File specifici da pubblicare")
    args = parser.parse_args(argv)

    pub = Publisher()
    code, msg = pub.publish(message=args.message, files=args.files or None)
    print(msg)
    return code


if __name__ == "__main__":
    sys.exit(main())
