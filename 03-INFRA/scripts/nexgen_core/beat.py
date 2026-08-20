"""Il battito di liveness e monitoraggio dipendenze per NeXgen Engine v2.

Compiti del battito (orario, indipendente dal Guard: gira senza mai tenere
il lock della guardia):
1. Liveness: controlla che il Guard sia arrivato in fondo recentemente (file
   agent-guard-liveness). Questo file risponde a UNA domanda sola e non va
   mai condiviso con l'antirimbalzo degli allarmi del Megafono: condividerlo
   è ciò che congelò la liveness dietro il debounce.
2. Dependency Watch: ispeziona a monte le dipendenze di terze parti pinnate
   e scrive third-party-upgrades.md nella cartella di stato. Non applica
   nulla e non notifica mai.
3. Self-Upgrader non presidiato: applica un salto di patch rilasciato, se
   c'è, senza chiedere. Rifiuta da solo un salto minor o major.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from nexgen_core.depwatch import run_depwatch
from nexgen_core.megaphone import Megaphone
from nexgen_core.paths import resolve_engine_root, resolve_state_dir, resolve_vault_data
from nexgen_core.updater import EngineUpdater

LIVENESS_FILE_NAME = "agent-guard-liveness"
MAX_LIVENESS_AGE_HOURS = 2.5


class Heartbeat:
    """Gestore del battito orario."""

    def __init__(
        self,
        state_dir: Path | None = None,
        vault_data: Path | None = None,
        engine_root: Path | None = None,
    ) -> None:
        self.state_dir = resolve_state_dir(override=state_dir)
        self.vault_data = resolve_vault_data(override=vault_data)
        self.engine_root = resolve_engine_root(override=engine_root)
        self.megaphone = Megaphone(state_dir=self.state_dir)
        self.liveness_file = self.state_dir / LIVENESS_FILE_NAME

    def record_liveness(self) -> None:
        """Registra il completamento con successo di un ciclo Guard."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.liveness_file.write_text(str(time.time()), encoding="utf-8")

    def check_liveness(self) -> tuple[bool, str]:
        """Verifica se il Guard ha girato entro i limiti previsti."""
        if not self.liveness_file.is_file():
            return False, "Nessun ciclo di guardia precedentemente registrato"

        try:
            last_ts = float(self.liveness_file.read_text(encoding="utf-8").strip())
            elapsed = time.time() - last_ts
            if elapsed > MAX_LIVENESS_AGE_HOURS * 3600:
                hours = elapsed / 3600
                msg = f"Il ciclo di sincronizzazione è fermo da {hours:.1f} ore."
                self.megaphone.send_alert(
                    title="Sincronizzazione agente non attiva",
                    message=msg,
                    action="Esegui 'agent-sync apply' sul terminale per verificare lo stato.",
                    alert_key="guard_stale"
                )
                return False, msg
            return True, f"Guard attivo (ultimo completamento {elapsed/60:.0f} minuti fa)"
        except Exception as exc:
            return False, f"Errore lettura liveness: {exc}"

    def run_dependency_watch(self) -> dict[str, Any]:
        """Ispeziona a monte i pin di terze parti. Non applica e non notifica
        mai: un guasto qui è manutenzione mancata, non un motivo per fermare
        il resto del battito."""
        try:
            result = run_depwatch(vault_data=self.vault_data, state_dir=self.state_dir)
            return {"ok": True, "wrote": result.wrote, "stale": sum(f.stale for f in result.findings)}
        except Exception as exc:  # noqa: BLE001 - manutenzione best-effort, non deve mai fermare il battito
            return {"ok": False, "error": str(exc)}

    def run_self_upgrade(self) -> dict[str, Any]:
        """Tenta un aggiornamento non presidiato, con il tetto di patch
        dell'updater. Usa il vault/engine di QUESTO Heartbeat, non l'ambiente
        di processo, così un Heartbeat costruito per i test non tocca mai
        l'installazione reale dell'host."""
        try:
            environ = {
                **os.environ,
                "AGENT_ENGINE_ROOT": str(self.engine_root),
                "AGENT_VAULT_DATA": str(self.vault_data),
            }
            exit_code = EngineUpdater.main(["--unattended"], environ=environ)
            return {"ok": exit_code == 0, "exit_code": exit_code}
        except Exception as exc:  # noqa: BLE001 - manutenzione best-effort, non deve mai fermare il battito
            return {"ok": False, "error": str(exc)}

    def run_beat(self) -> dict[str, Any]:
        """Esegue il ciclo completo del battito: la domanda di liveness, poi
        le due manutenzioni che il contratto affida a questo posto perché
        gira regolarmente senza tenere il lock della guardia."""
        liveness_ok, liveness_msg = self.check_liveness()
        return {
            "liveness_ok": liveness_ok,
            "liveness_msg": liveness_msg,
            "dependency_watch": self.run_dependency_watch(),
            "self_upgrade": self.run_self_upgrade(),
            "timestamp": time.time(),
        }
