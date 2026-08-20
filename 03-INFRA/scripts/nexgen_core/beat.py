"""Il battito di liveness e monitoraggio dipendenze per NeXgen Engine v2.

Compiti del battito (orario, indipendente dal Guard):
1. Liveness: controlla che il Guard sia arrivato in fondo recentemente (file agent-guard-liveness).
2. Dependency Watch: ispeziona le dipendenze di terze parti e scrive third-party-upgrades.md senza mai notificare né alterare il comportamento.
3. Self-Upgrader: verifica la presenza di aggiornamenti stabili rilasciati del motore.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from nexgen_core.megaphone import Megaphone
from nexgen_core.paths import resolve_engine_root, resolve_state_dir, resolve_vault_data

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

    def run_beat(self) -> dict[str, Any]:
        """Esegue il ciclo completo del battito."""
        liveness_ok, liveness_msg = self.check_liveness()
        return {
            "liveness_ok": liveness_ok,
            "liveness_msg": liveness_msg,
            "timestamp": time.time(),
        }
