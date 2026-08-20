"""Il confine fra la presentazione dei runtime e l'identità privata.

Due cose diverse, e confonderle è già costato sei giorni di silenzio:

- una **violazione di confine** è una memoria strutturata che vive accanto al
  Vault. Il Vault è la memoria; una seconda memoria significa due verità che
  divergono, e va segnalata come guasto.
- un **difetto di forma** è un metadato mancante o malformato nello spazio
  personale. Va detto, ma non può valere quanto il primo: il 14 agosto una
  riga di frontmatter assente ha fatto uscire con errore la guardia
  dell'identità, systemd ha cancellato il sync a valle, e per sei giorni
  nessuno se n'è accorto perché il megafono viveva dentro il servizio morto.

Lo strumento che *ripara* questo confine vive nei dati privati ed è fuori dal
perimetro del motore pubblico. Qui c'è solo il giudizio, che è il mestiere del
dottore.
"""
from __future__ import annotations

import re
from pathlib import Path

from nexgen_core.report import CheckOutcome, Severity

#: Dove ogni runtime tiene una memoria *strutturata* per conto proprio. Le
#: trascrizioni di sessione non sono in questo elenco di proposito: sono
#: materiale grezzo da distillare, non una seconda verità.
NATIVE_MEMORY_STORES: dict[str, tuple[str, ...]] = {
    "claude": (".claude/memory",),
}

#: Il frontmatter minimo che rende leggibile lo spazio personale.
REQUIRED_FRONTMATTER = ("status",)

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)


def _self_file(vault_data: Path) -> Path:
    return vault_data / "03-INFRA" / "agent-universal-layer" / "agent-self.md"


def check_agent_self(vault_data: Path) -> CheckOutcome:
    """Lo spazio personale esiste ed è leggibile?

    Assente non è un guasto: nessuno è obbligato ad averlo. Presente ma
    illeggibile lo è, perché qualcosa lo ha rotto senza che l'utente scegliesse.
    """
    self_file = _self_file(vault_data)
    if not self_file.is_file():
        return CheckOutcome(
            id="identity.self_present",
            severity=Severity.UNDETERMINED,
            message="Lo spazio personale dell'assistente non esiste ancora su questa macchina.",
            action="Non serve fare niente, a meno che tu non lo voglia creare.",
        )
    try:
        self_file.read_text(encoding="utf-8")
    except OSError as exc:
        return CheckOutcome(
            id="identity.self_present",
            severity=Severity.BROKEN,
            message="Lo spazio personale dell'assistente esiste ma non si riesce a leggere.",
            action=f"Controlla i permessi di {self_file}",
            detail=str(exc),
        )
    return CheckOutcome(
        id="identity.self_present",
        severity=Severity.OK,
        message="Spazio personale presente e leggibile",
    )


def check_agent_self_metadata(vault_data: Path) -> CheckOutcome:
    """Il frontmatter dello spazio personale è a posto?

    Difetto di forma: si riporta, non ferma niente. È esattamente il caso che
    il 14 agosto ha mandato giù l'intero layer per una riga mancante.
    """
    self_file = _self_file(vault_data)
    if not self_file.is_file():
        return CheckOutcome(
            id="identity.self_metadata",
            severity=Severity.OK,
            message="Nessun frontmatter da controllare",
        )
    try:
        content = self_file.read_text(encoding="utf-8")
    except OSError:
        return CheckOutcome(
            id="identity.self_metadata",
            severity=Severity.UNDETERMINED,
            message="Non si riesce a leggere lo spazio personale per controllarne i metadati.",
        )

    match = _FRONTMATTER_RE.match(content)
    if match is None:
        return CheckOutcome(
            id="identity.self_metadata",
            severity=Severity.BROKEN,
            message="Lo spazio personale non ha l'intestazione dei metadati.",
            action=f"Aggiungi un blocco '---' con 'status:' in cima a {self_file.name}",
            detail="Difetto di forma: non impedisce a niente di funzionare.",
        )

    body = match.group(1)
    missing = [key for key in REQUIRED_FRONTMATTER if not re.search(rf"^\s*{key}\s*:", body, re.M)]
    if missing:
        return CheckOutcome(
            id="identity.self_metadata",
            severity=Severity.BROKEN,
            message=f"Allo spazio personale manca {', '.join(missing)} nei metadati.",
            action=f"Aggiungi '{missing[0]}:' nell'intestazione di {self_file.name}",
            detail="Difetto di forma: non impedisce a niente di funzionare.",
        )
    return CheckOutcome(
        id="identity.self_metadata",
        severity=Severity.OK,
        message="Metadati dello spazio personale a posto",
    )


def check_native_memory_boundary(home: Path) -> CheckOutcome:
    """Esiste una memoria strutturata accanto al Vault?

    Violazione di confine: due memorie sono due verità, e prima o poi
    divergono. Non si cancella niente — si dice che c'è.
    """
    found: list[str] = []
    for runtime, locations in NATIVE_MEMORY_STORES.items():
        for relative in locations:
            store = home / relative
            if not store.is_dir():
                continue
            populated = any(p.is_file() for p in store.rglob("*"))
            if populated:
                found.append(f"{runtime} ({store})")

    if not found:
        return CheckOutcome(
            id="identity.native_memory_boundary",
            severity=Severity.OK,
            message="Nessuna memoria nativa in parallelo al Vault",
        )

    return CheckOutcome(
        id="identity.native_memory_boundary",
        severity=Severity.BROKEN,
        message=(
            f"{'Un runtime tiene' if len(found) == 1 else 'Alcuni runtime tengono'} "
            f"una memoria propria accanto al Vault: {', '.join(found)}. "
            f"Due memorie separate finiscono per dire cose diverse."
        ),
        action="nexgen inventory   # per vedere cosa contiene prima di decidere",
        detail="Niente viene cancellato: la decisione è tua.",
    )
