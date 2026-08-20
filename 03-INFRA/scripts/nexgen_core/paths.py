"""Risoluzione dei percorsi del layer, in un posto solo.

Questa cascata di variabili d'ambiente esisteva in quindici copie letterali
sparse per il package, e due di quelle copie avevano già divergito: una
leggeva `sync/remotes.yaml` in un modo, l'altra in un altro. Ogni componente
che ha bisogno di sapere dove sono i dati, il motore o lo stato lo chiede qui.

Le precedenze sono quelle storiche e non vanno cambiate senza una finestra di
compatibilità: sono contratto per chi ha già una macchina configurata.
"""
from __future__ import annotations

import os
from pathlib import Path

#: Nome della cartella dati sotto la home, quando nessuna variabile la nomina.
DEFAULT_VAULT_DIRNAME = "KnowledgeVault"

#: Cartella di stato locale alla macchina. Non si sincronizza mai.
STATE_DIRNAME = ".nexgen-engine"

#: Sottocartella del clone del motore che contiene scripts/ e agent-universal-layer/.
ENGINE_SUBDIR = "03-INFRA"


def resolve_home(home: Path | None = None) -> Path:
    """La home dell'utente, sovrascrivibile per i test."""
    return Path(home) if home is not None else Path.home()


def resolve_vault_data(home: Path | None = None, override: Path | None = None) -> Path:
    """Dove vivono i dati privati (il Vault).

    Precedenza: argomento esplicito, `AGENT_VAULT_DATA`, `KNOWLEDGE_VAULT_PATH`,
    infine `~/KnowledgeVault`.
    """
    if override is not None:
        return Path(override)
    env = os.environ.get("AGENT_VAULT_DATA") or os.environ.get("KNOWLEDGE_VAULT_PATH")
    if env:
        return Path(env)
    return resolve_home(home) / DEFAULT_VAULT_DIRNAME


def resolve_engine_root(home: Path | None = None, override: Path | None = None) -> Path:
    """Dove vive il motore installato (la cartella `03-INFRA` del suo clone).

    Precedenza: argomento esplicito, `AGENT_ENGINE_ROOT`, infine
    `~/.nexgen-engine/03-INFRA`.
    """
    if override is not None:
        return Path(override)
    env = os.environ.get("AGENT_ENGINE_ROOT")
    if env:
        return Path(env)
    return resolve_home(home) / STATE_DIRNAME / ENGINE_SUBDIR


def resolve_state_dir(home: Path | None = None, override: Path | None = None) -> Path:
    """Dove vive lo stato locale alla macchina (lock, timbri, debounce).

    Precedenza: argomento esplicito, `AGENT_STATE_DIR`, infine `~/.nexgen-engine`.
    """
    if override is not None:
        return Path(override)
    env = os.environ.get("AGENT_STATE_DIR")
    if env:
        return Path(env)
    return resolve_home(home) / STATE_DIRNAME


def canonical_instructions(vault_data: Path | None = None) -> Path:
    """Il file di istruzioni canonico da cui ogni runtime deriva il proprio."""
    root = vault_data if vault_data is not None else resolve_vault_data()
    return Path(root) / ENGINE_SUBDIR / "agent-universal-layer" / "instructions" / "AGENTS.md"


def mcp_manifest(vault_data: Path | None = None) -> Path:
    """Il manifest dei connettori, unica fonte della configurazione MCP."""
    root = vault_data if vault_data is not None else resolve_vault_data()
    return Path(root) / ENGINE_SUBDIR / "agent-universal-layer" / "mcp" / "manifest.yaml"


def skills_manifest(vault_data: Path | None = None) -> Path:
    """Il manifest delle skill, unica fonte di cosa viene materializzato."""
    root = vault_data if vault_data is not None else resolve_vault_data()
    return Path(root) / ENGINE_SUBDIR / "agent-universal-layer" / "skills" / "skills.manifest.yaml"


def remotes_config(vault_data: Path | None = None) -> Path:
    """La dichiarazione del remoto autoritativo e dei mirror."""
    root = vault_data if vault_data is not None else resolve_vault_data()
    return Path(root) / ENGINE_SUBDIR / "agent-universal-layer" / "sync" / "remotes.yaml"
