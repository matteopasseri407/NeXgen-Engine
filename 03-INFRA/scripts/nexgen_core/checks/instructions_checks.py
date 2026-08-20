"""Controlli sulle istruzioni canoniche e sull'igiene del bootstrap AGENTS.md.

Port dalle sezioni "Canonical instructions" e "Canonical bootstrap hygiene"
del doctor bash della release. Ogni CLI deriva le proprie istruzioni da UN
solo file canonico (`instructions/AGENTS.md`); questi controlli verificano
che il puntatore di ciascuna CLI sia ancora quello, e che il bootstrap stesso
resti dentro i propri budget dimensionali e non punti a note sparite.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

from nexgen_core.jsonc import parse_jsonc
from nexgen_core.paths import canonical_instructions
from nexgen_core.renderer import McpRenderer
from nexgen_core.report import CheckOutcome, Severity

#: Budget dimensione (byte) del bootstrap canonico AGENTS.md, oltre il quale
#: conviene spostare contenuto specifico di un compito in una nota
#: load-on-demand. Sovrascrivibile per installazioni con convenzioni diverse.
BOOTSTRAP_MAX_BYTES = int(os.environ.get("NEXGEN_BOOTSTRAP_MAX_BYTES", "40000"))

#: Budget dimensione (byte) per ciascuna nota di dettaglio caricata on-demand
#: (i file *.md sotto la radice 03-INFRA del Vault).
NOTE_MAX_BYTES = int(os.environ.get("NEXGEN_NOTE_MAX_BYTES", "16000"))

_POINTER_RE = re.compile(r"`([^`]+\.md)`")


def check_canonical_instructions_present(vault_data: Path) -> CheckOutcome:
    """Il file di istruzioni canonico deve esistere: è la sorgente unica da
    cui ogni CLI deriva le proprie."""
    canon = canonical_instructions(vault_data)
    if not canon.is_file():
        return CheckOutcome(
            id="instructions.canonical_present",
            severity=Severity.BROKEN,
            message=f"Il file di istruzioni canonico non esiste ({canon}).",
            action="Ripristina 03-INFRA/agent-universal-layer/instructions/AGENTS.md dal Vault.",
        )
    return CheckOutcome(
        id="instructions.canonical_present",
        severity=Severity.OK,
        message="File di istruzioni canonico AGENTS.md presente",
    )


def check_claude_pointer(vault_data: Path, home: Path) -> CheckOutcome:
    """~/CLAUDE.md deve essere un puntatore leggero al canonico, non una
    copia o un symlink: una copia diverge in silenzio dal canonico."""
    canon = canonical_instructions(vault_data)
    claude_file = home / "CLAUDE.md"

    if claude_file.is_symlink() or not claude_file.is_file():
        return CheckOutcome(
            id="instructions.claude_pointer",
            severity=Severity.BROKEN,
            message=f"~/CLAUDE.md deve essere un file di testo puntatore, non assente o un symlink ({claude_file}).",
            action=f"Scrivi in {claude_file} un breve puntatore che referenzi {canon}.",
        )

    try:
        content = claude_file.read_text(encoding="utf-8")
    except OSError as exc:
        return CheckOutcome(
            id="instructions.claude_pointer",
            severity=Severity.UNDETERMINED,
            message=f"Impossibile leggere ~/CLAUDE.md: {exc}",
        )

    if str(canon) in content:
        return CheckOutcome(
            id="instructions.claude_pointer",
            severity=Severity.OK,
            message="~/CLAUDE.md punta correttamente al file canonico AGENTS.md",
        )
    return CheckOutcome(
        id="instructions.claude_pointer",
        severity=Severity.BROKEN,
        message="~/CLAUDE.md non referenzia il percorso del file canonico AGENTS.md (rischio di copia duplicata che diverge in silenzio).",
        action=f"Sostituisci ~/CLAUDE.md con un puntatore che referenzi {canon}.",
    )


def _pointer_outcome(cli_name: str, id_: str, pointer_file: Path, canon: Path, *, installed: bool) -> CheckOutcome | None:
    """Un puntatore CLI->canonico: OK se allineato, BROKEN se presente ma
    divergente o se la CLI è installata e il puntatore manca del tutto. Una
    CLI mai installata non è un guasto: non ha alcun percorso di codice che
    creerà mai questo file, quindi non si riporta affatto."""
    if pointer_file.exists() or pointer_file.is_symlink():
        try:
            aligned = canon.is_file() and pointer_file.resolve() == canon.resolve()
        except OSError:
            aligned = False
        if aligned:
            return CheckOutcome(id=id_, severity=Severity.OK, message=f"{cli_name} punta al file canonico AGENTS.md")
        return CheckOutcome(
            id=id_,
            severity=Severity.BROKEN,
            message=f"Le istruzioni di {cli_name} ({pointer_file}) non puntano al file canonico AGENTS.md.",
            action="Esegui 'agent-sync apply' per ricreare il puntatore.",
        )
    if installed:
        return CheckOutcome(
            id=id_,
            severity=Severity.BROKEN,
            message=f"{cli_name} è installato ma non ha ancora il puntatore alle istruzioni canoniche ({pointer_file}).",
            action="Esegui 'agent-sync apply' per generare il puntatore.",
        )
    return None


def check_cli_instruction_pointers(vault_data: Path, home: Path) -> list[CheckOutcome]:
    """Puntatori Codex e Antigravity al file canonico AGENTS.md."""
    canon = canonical_instructions(vault_data)
    targets = (
        ("Codex", "instructions.codex_pointer", home / ".codex" / "AGENTS.md", "codex"),
        ("Antigravity", "instructions.antigravity_pointer", home / ".gemini" / "config" / "AGENTS.md", "agy"),
    )
    outcomes: list[CheckOutcome] = []
    for cli_name, id_, pointer_file, binary in targets:
        outcome = _pointer_outcome(cli_name, id_, pointer_file, canon, installed=shutil.which(binary) is not None)
        if outcome is not None:
            outcomes.append(outcome)
    return outcomes


def check_opencode_instructions(vault_data: Path, home: Path) -> CheckOutcome | None:
    """L'array `instructions` della config OpenCode deve includere il file
    canonico AGENTS.md. OpenCode mai lanciata (nessuna config) non è un
    guasto e non si riporta."""
    renderer = McpRenderer(vault_data=vault_data, home=home)
    cfg_file = renderer._opencode_config_path()
    installed = shutil.which("opencode") is not None or (home / ".opencode" / "bin" / "opencode").is_file()

    if not cfg_file.is_file():
        if installed:
            return CheckOutcome(
                id="instructions.opencode_pointer",
                severity=Severity.BROKEN,
                message=f"OpenCode è installato ma manca il file di configurazione ({cfg_file}).",
                action="Esegui 'agent-sync apply' per generarlo.",
            )
        return None

    try:
        raw = cfg_file.read_text(encoding="utf-8")
        data = parse_jsonc(raw) if cfg_file.suffix == ".jsonc" else json.loads(raw)
    except (OSError, ValueError) as exc:
        return CheckOutcome(
            id="instructions.opencode_pointer",
            severity=Severity.UNDETERMINED,
            message=f"Impossibile analizzare la configurazione OpenCode ({cfg_file}): {exc}",
        )

    entries = data.get("instructions", []) if isinstance(data, dict) else []
    canon_suffix = "/agent-universal-layer/instructions/agents.md"
    matches = sum(
        1
        for item in entries
        if isinstance(item, str) and item.replace("\\", "/").rstrip("/").lower().endswith(canon_suffix)
    )

    if matches == 0:
        return CheckOutcome(
            id="instructions.opencode_pointer",
            severity=Severity.BROKEN,
            message="Le 'instructions' di OpenCode non includono il file canonico AGENTS.md.",
            action="Esegui 'agent-sync apply' per registrarlo.",
        )
    return CheckOutcome(
        id="instructions.opencode_pointer",
        severity=Severity.OK,
        message="OpenCode carica il file canonico AGENTS.md",
    )


def check_bootstrap_size_budget(vault_data: Path) -> CheckOutcome | None:
    """Il bootstrap AGENTS.md deve restare dentro il proprio budget
    dimensionale. Se il canonico manca è già segnalato altrove."""
    canon = canonical_instructions(vault_data)
    if not canon.is_file():
        return None

    size = canon.stat().st_size
    if size > BOOTSTRAP_MAX_BYTES:
        return CheckOutcome(
            id="instructions.bootstrap_size_budget",
            severity=Severity.BROKEN,
            message=f"Il bootstrap AGENTS.md pesa {size} byte, oltre il budget di {BOOTSTRAP_MAX_BYTES}.",
            action="Sposta contenuto specifico di un compito in una nota load-on-demand, richiamata solo quando serve.",
        )
    return CheckOutcome(
        id="instructions.bootstrap_size_budget",
        severity=Severity.OK,
        message=f"Bootstrap AGENTS.md entro il budget ({size}/{BOOTSTRAP_MAX_BYTES} byte)",
    )


def check_bootstrap_notes_size(vault_data: Path) -> CheckOutcome | None:
    """Le note di dettaglio caricate on-demand (03-INFRA/*.md) devono
    restare dentro il proprio budget dimensionale."""
    canon = canonical_instructions(vault_data)
    if not canon.is_file():
        return None

    notes_dir = vault_data / "03-INFRA"
    if not notes_dir.is_dir():
        return None

    oversized = [
        f"{note.name} ({note.stat().st_size}b)"
        for note in sorted(notes_dir.glob("*.md"))
        if note.stat().st_size > NOTE_MAX_BYTES
    ]
    if oversized:
        return CheckOutcome(
            id="instructions.bootstrap_notes_size",
            severity=Severity.BROKEN,
            message=f"Nota/e di dettaglio oltre il budget di {NOTE_MAX_BYTES} byte: {', '.join(oversized)}.",
            action="Valuta di dividere la nota in sezioni più piccole caricate on-demand.",
        )
    return CheckOutcome(
        id="instructions.bootstrap_notes_size",
        severity=Severity.OK,
        message=f"Note di dettaglio entro il budget di {NOTE_MAX_BYTES} byte",
    )


def check_bootstrap_pointer_integrity(vault_data: Path) -> CheckOutcome | None:
    """Ogni puntatore load-on-demand citato in backtick nel bootstrap
    (`03-INFRA/...md`, `99-INDEX/...md`, ecc.) deve risolvere a un file
    esistente sotto il Vault. Le radici valide sono derivate dal filesystem
    del Vault stesso, non da un elenco cablato."""
    canon = canonical_instructions(vault_data)
    if not canon.is_file():
        return None

    try:
        content = canon.read_text(encoding="utf-8")
    except OSError as exc:
        return CheckOutcome(
            id="instructions.bootstrap_pointer_integrity",
            severity=Severity.UNDETERMINED,
            message=f"Impossibile leggere il bootstrap AGENTS.md: {exc}",
        )

    top_dirs = {p.name for p in vault_data.iterdir() if p.is_dir() and not p.name.startswith(".")}

    missing: list[str] = []
    checked = 0
    for ref in _POINTER_RE.findall(content):
        if "<" in ref or ">" in ref:
            continue
        first_segment = ref.split("/", 1)[0]
        if first_segment not in top_dirs:
            continue
        checked += 1
        if not (vault_data / ref).is_file():
            missing.append(ref)

    if checked == 0:
        return CheckOutcome(
            id="instructions.bootstrap_pointer_integrity",
            severity=Severity.OK,
            message="Nessun puntatore load-on-demand da verificare nel bootstrap",
        )
    if missing:
        return CheckOutcome(
            id="instructions.bootstrap_pointer_integrity",
            severity=Severity.BROKEN,
            message=f"Puntatore/i load-on-demand nel bootstrap che non risolvono a un file esistente: {', '.join(missing)}.",
            action="Correggi o rimuovi i riferimenti alle note rinominate/rimosse in AGENTS.md.",
        )
    return CheckOutcome(
        id="instructions.bootstrap_pointer_integrity",
        severity=Severity.OK,
        message=f"Tutti i {checked} puntatori load-on-demand del bootstrap risolvono correttamente",
    )
