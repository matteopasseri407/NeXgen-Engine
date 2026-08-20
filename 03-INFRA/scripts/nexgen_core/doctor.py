#!/usr/bin/env python3
"""Il Giudice (Doctor): verificatore universale di allineamento per NeXgen Engine v2.

Unica implementazione condivisa per Linux e Windows:
- Elimina completamente la duplicazione tra Bash e PowerShell.
- Restituisce esiti strutturati con rimedi automatici (nessun falso allarme).
- Rispetta il silenzio operativo: conta i successi e segnala solo ciò che richiede attenzione.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.checks.env_checks import check_state_dir, check_vault_path
from nexgen_core.checks.git_checks import check_git_alignment
from nexgen_core.checks.identity_checks import check_agent_self
from nexgen_core.checks.mcp_checks import check_mcp_configs_rendered, check_mcp_manifest
from nexgen_core.checks.security_checks import check_required_rules, check_tokens_in_env
from nexgen_core.checks.skill_checks import (
    check_skill_library_and_index,
    check_skills_manifest,
)
from nexgen_core.report import Report


class Doctor:
    """Giudice dello stato di allineamento del layer agentico."""

    def __init__(
        self,
        vault_data: Path | None = None,
        state_dir: Path | None = None,
        home: Path | None = None,
    ) -> None:
        self.home = home or Path.home()
        _v = vault_data or Path(os.environ.get("AGENT_VAULT_DATA") or os.environ.get("KNOWLEDGE_VAULT_PATH") or str(self.home / "KnowledgeVault"))
        self.vault_data = _v
        self.state_dir = state_dir or Path(os.environ.get("AGENT_STATE_DIR", self.home / ".nexgen-engine"))

    def run_diagnostics(self, apply_remedies: bool = False) -> Report:
        """Esegue tutti i controlli registrati (in sola lettura di default)."""
        report = Report()

        # 1. Controlli ambiente e directory
        report.add(check_state_dir(self.state_dir), apply_remedy=apply_remedies)
        report.add(check_vault_path(self.vault_data), apply_remedy=apply_remedies)

        # 2. Controlli Git (se il Vault esiste)
        if self.vault_data.is_dir():
            report.add(check_git_alignment(self.vault_data), apply_remedy=apply_remedies)

            # 3. Controlli MCP
            manifest_mcp = self.vault_data / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
            report.add(check_mcp_manifest(manifest_mcp), apply_remedy=apply_remedies)
            report.add(check_mcp_configs_rendered(self.vault_data, self.home), apply_remedy=apply_remedies)

            # 4. Controlli Skill
            manifest_skills = self.vault_data / "03-INFRA" / "agent-universal-layer" / "skills" / "skills.manifest.yaml"
            report.add(check_skills_manifest(manifest_skills), apply_remedy=apply_remedies)
            report.add(check_skill_library_and_index(self.vault_data, self.home), apply_remedy=apply_remedies)

            # 5. Controlli Identità
            report.add(check_agent_self(self.vault_data), apply_remedy=apply_remedies)

            # 6. Controlli bootstrap e segreti in env (port dalla release)
            report.add(check_required_rules(self.vault_data), apply_remedy=apply_remedies)
            report.add(check_tokens_in_env(self.vault_data), apply_remedy=apply_remedies)

        return report


def main(argv: list[str] | None = None) -> int:
    """Entry point CLI per il comando agent-doctor."""
    parser = argparse.ArgumentParser(description="NeXgen Engine Doctor (v2) - Diagnostica e verifica allineamento")
    parser.add_argument("-v", "--verbose", action="store_true", help="Mostra tutti i controlli eseguiti compresi quelli superati")
    parser.add_argument("--strict", action="store_true", help="Modalità rigorosa: tratta anche gli stati indeterminati come non conformi")
    parser.add_argument("--json", action="store_true", help="Stampa l'output in formato JSON")
    parser.add_argument("--summary", action="store_true", help="Stampa il riepilogo con conteggi FAIL=N OK=N")
    parser.add_argument("--fix", "--remedy", action="store_true", help="Applica i rimedi automatici per i problemi riparabili")

    args = parser.parse_args(argv)

    doc = Doctor()
    report = doc.run_diagnostics(apply_remedies=args.fix)

    if args.json:
        print(report.format_json())
    elif args.summary:
        fail_count = len(report.broken)
        ok_count = report.ok_count
        undet_count = len(report.undetermined)
        print(f"FAIL={fail_count} OK={ok_count} UNDETERMINED={undet_count}")
        if fail_count > 0:
            for b in report.broken:
                print(f"  ✗ {b.message}")
    else:
        print(report.format_human(verbose=args.verbose))

    return report.exit_code(strict=args.strict)


if __name__ == "__main__":
    sys.exit(main())
