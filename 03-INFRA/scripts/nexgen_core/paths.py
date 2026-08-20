"""Path resolution for the layer, in one place.

This environment-variable cascade used to exist as fifteen literal copies
scattered across the package, and two of those copies had already drifted:
one read `sync/remotes.yaml` one way, the other a different way. Any
component that needs to know where the data, the engine, or the state live
asks here.

The precedence order is the historical one and must not change without a
compatibility window: it is a contract for anyone who already has a machine
configured.
"""
from __future__ import annotations

import os
from pathlib import Path

#: Name of the data folder under the home directory, when no variable names it.
DEFAULT_VAULT_DIRNAME = "KnowledgeVault"

#: Machine-local state folder. Never synced.
STATE_DIRNAME = ".nexgen-engine"

#: Subfolder of the engine clone that contains scripts/ and agent-universal-layer/.
ENGINE_SUBDIR = "03-INFRA"


def resolve_home(home: Path | None = None) -> Path:
    """The user's home directory, overridable for tests."""
    return Path(home) if home is not None else Path.home()


def resolve_vault_data(home: Path | None = None, override: Path | None = None) -> Path:
    """Where the private data (the Vault) lives.

    Precedence: explicit argument, `AGENT_VAULT_DATA`, `KNOWLEDGE_VAULT_PATH`,
    finally `~/KnowledgeVault`.
    """
    if override is not None:
        return Path(override)
    env = os.environ.get("AGENT_VAULT_DATA") or os.environ.get("KNOWLEDGE_VAULT_PATH")
    if env:
        return Path(env)
    return resolve_home(home) / DEFAULT_VAULT_DIRNAME


def resolve_engine_root(home: Path | None = None, override: Path | None = None) -> Path:
    """Where the installed engine lives (the `03-INFRA` folder of its clone).

    Precedence: explicit argument, `AGENT_ENGINE_ROOT`, finally
    `~/.nexgen-engine/03-INFRA`.
    """
    if override is not None:
        return Path(override)
    env = os.environ.get("AGENT_ENGINE_ROOT")
    if env:
        return Path(env)
    return resolve_home(home) / STATE_DIRNAME / ENGINE_SUBDIR


def resolve_state_dir(home: Path | None = None, override: Path | None = None) -> Path:
    """Where the machine-local state lives (locks, timestamps, debounce).

    Precedence: explicit argument, `AGENT_STATE_DIR`, finally `~/.nexgen-engine`.
    """
    if override is not None:
        return Path(override)
    env = os.environ.get("AGENT_STATE_DIR")
    if env:
        return Path(env)
    return resolve_home(home) / STATE_DIRNAME


def canonical_instructions(vault_data: Path | None = None) -> Path:
    """The canonical instructions file from which every runtime derives its own."""
    root = vault_data if vault_data is not None else resolve_vault_data()
    return Path(root) / ENGINE_SUBDIR / "agent-universal-layer" / "instructions" / "AGENTS.md"


def mcp_manifest(vault_data: Path | None = None) -> Path:
    """The connector manifest, the single source of the MCP configuration."""
    root = vault_data if vault_data is not None else resolve_vault_data()
    return Path(root) / ENGINE_SUBDIR / "agent-universal-layer" / "mcp" / "manifest.yaml"


def skills_manifest(vault_data: Path | None = None) -> Path:
    """The skills manifest, the single source of what gets materialized."""
    root = vault_data if vault_data is not None else resolve_vault_data()
    return Path(root) / ENGINE_SUBDIR / "agent-universal-layer" / "skills" / "skills.manifest.yaml"


def remotes_config(vault_data: Path | None = None) -> Path:
    """The declaration of the authoritative remote and its mirrors."""
    root = vault_data if vault_data is not None else resolve_vault_data()
    return Path(root) / ENGINE_SUBDIR / "agent-universal-layer" / "sync" / "remotes.yaml"
