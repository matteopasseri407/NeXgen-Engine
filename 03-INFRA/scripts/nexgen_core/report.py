"""Sistema di reporting ed esiti per NeXgen Engine v2.

Ogni controllo restituisce un oggetto strutturato con quattro campi principali
(id, severity, message, action) più un eventuale rimedio automatico (remedy).

Regole fondamentali:
1. Se c'è un remedy e il controllo è broken, viene eseguito in silenzio.
2. Se non c'è rimedio e lo stato è broken, viene riportato con messaggio chiaro e una sola azione.
3. Se lo stato è undetermined, viene segnalato distintamente da broken.
4. Se lo stato è ok, viene conteggiato e taciuto (salvo modalità verbose).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class Severity(str, Enum):
    """Livelli di severità per un controllo."""
    OK = "ok"
    BROKEN = "broken"
    UNDETERMINED = "undetermined"


@dataclass
class CheckOutcome:
    """Esito normalizzato di un controllo di sistema o configurazione."""

    id: str
    severity: Severity | str
    message: str
    action: str | None = None
    remedy: Callable[[], bool | None] | None = None
    detail: str | None = None
    remedied: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.severity, str):
            self.severity = Severity(self.severity.lower())

    def to_dict(self) -> dict[str, Any]:
        """Serializzazione in dizionario per log o output JSON."""
        return {
            "id": self.id,
            "severity": self.severity.value,
            "message": self.message,
            "action": self.action,
            "detail": self.detail,
            "remedied": self.remedied,
            "has_remedy": self.remedy is not None,
        }


@dataclass
class Report:
    """Raccoglitore e formattatore degli esiti dei controlli."""

    outcomes: list[CheckOutcome] = field(default_factory=list)
    log_entries: list[str] = field(default_factory=list)

    def add(self, outcome: CheckOutcome, apply_remedy: bool = True) -> CheckOutcome:
        """Aggiunge ed eventualmente applica il rimedio automatico a un esito."""
        if apply_remedy and outcome.remedy and outcome.severity == Severity.BROKEN:
            try:
                res = outcome.remedy()
                # Consideriamo successo se non lancia eccezioni e non ritorna esplicitamente False
                if res is not False:
                    outcome.remedied = True
                    outcome.severity = Severity.OK
                    log_msg = f"Rimedio automatico applicato con successo per [{outcome.id}]: {outcome.message}"
                    self.log_entries.append(log_msg)
            except Exception as exc:
                outcome.detail = f"{outcome.detail or ''} (Rimedio fallito: {exc})".strip()
                self.log_entries.append(f"Rimedio fallito per [{outcome.id}]: {exc}")

        self.outcomes.append(outcome)
        return outcome

    @property
    def ok_count(self) -> int:
        return sum(1 for o in self.outcomes if o.severity == Severity.OK)

    @property
    def broken(self) -> list[CheckOutcome]:
        return [o for o in self.outcomes if o.severity == Severity.BROKEN]

    @property
    def undetermined(self) -> list[CheckOutcome]:
        return [o for o in self.outcomes if o.severity == Severity.UNDETERMINED]

    @property
    def is_healthy(self) -> bool:
        return len(self.broken) == 0 and len(self.undetermined) == 0

    @property
    def has_failures(self) -> bool:
        return len(self.broken) > 0

    def exit_code(self, strict: bool = False) -> int:
        """Calcola l'exit code di sistema: 0 se tutto ok/rimediato, >0 se ci sono problemi."""
        if self.has_failures:
            return 1
        if strict and len(self.undetermined) > 0:
            return 1
        return 0

    def format_human(self, verbose: bool = False) -> str:
        """Formatta il referto per la lettura umana secondo le regole anti-rumore."""
        lines: list[str] = []

        # 1. Se ci sono controlli rotti non rimediati, li mostriamo con priorità
        if self.broken:
            lines.append("Problemi rilevati che richiedono il tuo intervento:")
            for item in self.broken:
                line = f"  - {item.message}"
                if item.action:
                    line += f"\n    Azione suggerita: {item.action}"
                if item.detail and verbose:
                    line += f"\n    [Dettaglio: {item.detail}]"
                lines.append(line)

        # 2. Se ci sono controlli indeterminati (es. assenza rete o servizio non raggiungibile)
        if self.undetermined:
            if lines:
                lines.append("")
            lines.append("Controlli che non è stato possibile verificare al momento:")
            for item in self.undetermined:
                line = f"  - {item.message}"
                if item.action:
                    line += f"\n    Azione suggerita: {item.action}"
                if item.detail and verbose:
                    line += f"\n    [Dettaglio: {item.detail}]"
                lines.append(line)

        # 3. Riepilogo o dettagli verbose dei controlli superati
        if verbose:
            if lines:
                lines.append("")
            lines.append(f"Controlli superati ({self.ok_count}):")
            for item in self.outcomes:
                if item.severity == Severity.OK:
                    remedy_tag = " (riparato automaticamente)" if item.remedied else ""
                    lines.append(f"  [OK] {item.id}: {item.message}{remedy_tag}")
        elif not self.broken and not self.undetermined:
            # Silenzioso o un'unica riga rassicurante
            lines.append(f"Tutto allineato e funzionante ({self.ok_count} controlli superati).")
        else:
            lines.append(f"\n({self.ok_count} controlli superati)")

        return "\n".join(lines)

    def format_json(self) -> str:
        """Restituisce il report completo in formato JSON."""
        return json.dumps({
            "healthy": self.is_healthy,
            "ok_count": self.ok_count,
            "broken_count": len(self.broken),
            "undetermined_count": len(self.undetermined),
            "broken": [o.to_dict() for o in self.broken],
            "undetermined": [o.to_dict() for o in self.undetermined],
            "all": [o.to_dict() for o in self.outcomes],
            "logs": self.log_entries,
        }, indent=2)
