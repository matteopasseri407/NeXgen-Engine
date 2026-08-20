"""Il contratto unico: un adattatore per CLI, un solo confine per aggiungerne una quinta.

La release v1 applicava postura + guardrail hook per quattro CLI dentro
agent_sync.py (~767 righe) con cinque mappe di rendering separate
(PERMISSION_RENDERERS, CODEX_POSTURE_RENDER, OPENCODE_POSTURE_RENDER,
ANTIGRAVITY_POSTURE_RENDER, PERMISSION_HOOK_TARGETS...) tutte nello stesso
file. Aggiungere una CLI significava toccarle tutte. Qui ogni CLI e' un file
che implementa questo contratto: aggiungerne una e' aggiungere un file.

Le postura viaggiano in vocabolario NEUTRO (bypass / accept-edits / ask), mai
nel dialetto di una CLI specifica -- la traduzione e' responsabilita' interna
di ciascun adattatore, mai del chiamante.
"""
from __future__ import annotations

import os
import shutil
import time
from abc import ABC, abstractmethod
from pathlib import Path

#: Vocabolario neutro dei tre livelli di postura che questo motore conosce.
#: Un adattatore che non ha un rendering verificato per uno di questi valori
#: lo ignora silenziosamente (apply_posture ritorna None) -- indovinare un
#: dialetto non verificato ha gia' causato un incidente in v1.
POSTURES = ("bypass", "accept-edits", "ask")


class GuardrailError(Exception):
    """Un'anomalia che impedisce di scrivere in sicurezza: config dell'utente
    malformata, un percorso che esce dalla cartella permessa, una forma
    inattesa in una chiave che questo motore possiede.

    Distinta apposta da "CLI non installata" o "postura non supportata qui",
    che sono normali e si esprimono con un semplice None -- questa e' per
    l'anomalia che non va mai fatta passare in silenzio, perche' altrimenti
    una postura che toglie i prompt potrebbe raggiungere il disco senza il
    guardrail che avrebbe dovuto accompagnarla.
    """


class Runtime(ABC):
    """Un adattatore per CLI. `home` viaggia esplicito su ogni chiamata (mai
    letto da Path.home() al suo interno) cosi' un test puo' puntare ogni
    metodo su un tmp_path e la home reale dell'utente non viene mai toccata
    per errore.
    """

    name: str

    @abstractmethod
    def is_installed(self, home: Path) -> bool:
        """Questa CLI e' davvero presente su questa macchina?

        Va dedotto dall'impronta del PRODOTTO -- il suo binario sul PATH, o
        un file che scrive solo lui al primo avvio -- mai dall'esistenza di
        una cartella o di un file di config che questo stesso layer crea
        (il renderer MCP scrive ~/.claude.json, ~/.codex/config.toml,
        opencode.jsonc e mcp_config.json ad ogni ciclo, installata o no: nessuno
        di questi e' un segnale valido). Questo difetto e' stato trovato due
        volte in 24 ore nella release precedente.
        """

    @abstractmethod
    def read_posture(self, home: Path) -> str | None:
        """La postura in vigore ORA, in vocabolario neutro, o None se la
        config di questa CLI non esiste o non ne esprime una."""

    @abstractmethod
    def apply_posture(self, home: Path, posture: str) -> str | None:
        """Traduce `posture` (vocabolario neutro) nel dialetto di questa CLI.

        Ritorna una riga di azione se ha scritto qualcosa, None se non c'era
        nulla da fare -- gia' corretto (idempotenza), oppure questa CLI non
        ha un rendering verificato per quel valore (skip silenzioso, mai un
        tentativo indovinato)."""

    @abstractmethod
    def install_guardrail(self, home: Path, hook_source: Path, engine_hooks_dir: Path) -> str | None:
        """Registra l'hook pre-esecuzione la cui POLICY vive in `hook_source`
        (corpo privato del Vault, identico per tutte le CLI). `engine_hooks_dir`
        e' la cartella pubblica dell'engine con gli adattatori sottili gia'
        pronti (agent-universal-layer/hooks/*.mjs) per le CLI il cui contratto
        nativo non parla lo stesso JSON di Claude; le CLI che non ne hanno
        bisogno la ignorano.

        Ritorna una riga di azione se ha cambiato qualcosa, None se gia' a
        posto o se questa CLI non ha alcun aggancio guardrail verificato."""

    # ---- helper condivisi, disponibili a ogni adattatore -------------------

    @staticmethod
    def backup(path: Path) -> Path | None:
        """Copia con timestamp di un file di config ESISTENTE dell'utente,
        fatta PRIMA di ogni scrittura. Ogni incidente che ha giustificato
        questo pacchetto e' iniziato con un file di config sovrascritto senza
        nulla da cui recuperarlo. Nessun backup per un file che non esiste
        ancora -- non c'e' nulla da preservare."""
        if not path.is_file():
            return None
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup_path = path.with_name(f"{path.name}.pre-permissions-{stamp}.bak")
        shutil.copy2(path, backup_path)
        return backup_path

    @staticmethod
    def atomic_write(path: Path, text: str) -> None:
        """Scrivi-poi-rinomina: un crash a meta' scrittura non deve mai
        lasciare una config troncata che la CLI non riesce piu' a leggere al
        prossimo avvio."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)

    @staticmethod
    def deploy_bytes(dst: Path, body: bytes) -> bool:
        """Copia `body` in `dst` solo se diverso (idempotenza: una guardia
        che gira ogni pochi minuti non deve riscrivere un file identico ad
        ogni ciclo). Ritorna True se ha scritto."""
        if dst.exists() and dst.read_bytes() == body:
            return False
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(body)
        return True
