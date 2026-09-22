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
import sys
from pathlib import Path

#: Name of the data folder under the home directory, when no variable names it.
DEFAULT_VAULT_DIRNAME = "KnowledgeVault"

#: Machine-local state folder. Never synced.
ENGINE_DIRNAME = ".nexgen-engine"

#: Where machine-local state lives, relative to the home. It is deliberately
#: NOT the engine's own checkout: the updater refuses to work on a dirty tree
#: and counts untracked files, so a lock and a liveness stamp written into
#: that checkout would make it dirty on every cycle and block every future
#: update — permanently, and without saying why. This is also exactly where
#: the previous release kept the same files, so a machine crossing over keeps
#: its history and, more importantly, the two versions contend for the same
#: lock while they briefly coexist.
STATE_SUBPATH = (".local", "state")

#: Subfolder of the engine clone that contains scripts/ and agent-universal-layer/.
ENGINE_SUBDIR = "03-INFRA"


def resolve_home(home: Path | None = None) -> Path:
    """The home the engine works against.

    `NEXGEN_HOME` moves everything the engine writes — launchers, runtime
    configurations, the skill library, scheduled units, state — into a
    directory of your choosing. That is how you run a development checkout
    beside a working installation without the two touching: the machine you
    actually use keeps its own commands, its own connectors and its own
    memories, and the checkout you are working on gets a home to itself.

    Without it, this is the real home, which is what an installed engine
    wants.
    """
    if home is not None:
        return Path(home)
    sandbox = os.environ.get("NEXGEN_HOME")
    if sandbox:
        return Path(sandbox).expanduser()
    return Path.home()


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
    return resolve_home(home) / ENGINE_DIRNAME / ENGINE_SUBDIR


def resolve_state_dir(home: Path | None = None, override: Path | None = None) -> Path:
    """Where the machine-local state lives (locks, timestamps, debounce).

    Precedence: explicit argument, `AGENT_STATE_DIR`, `XDG_STATE_HOME`,
    finally `~/.local/state`.
    """
    if override is not None:
        return Path(override)
    env = os.environ.get("AGENT_STATE_DIR")
    if env:
        return Path(env)
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg and home is None and not os.environ.get("NEXGEN_HOME"):
        return Path(xdg)
    return resolve_home(home).joinpath(*STATE_SUBPATH)


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


#: File names OpenCode itself resolves, in the order it prefers them. The
#: release used to resolve an EXISTING file with priority jsonc > json >
#: config.json; that precedence is a contract (an already-configured machine
#: must be updated on the file it actually reads, never on a fresh copy next
#: to it) and now lives here instead of in two copies that could diverge
#: (renderer MCP vs guardrail adapter).
OPENCODE_CONFIG_NAMES: tuple[str, ...] = ("opencode.jsonc", "opencode.json", "config.json")


def opencode_config_dir(home: Path | None = None) -> Path:
    """The directory holding OpenCode's global configuration."""
    return resolve_home(home) / ".config" / "opencode"


def opencode_config_candidates(home: Path | None = None) -> list[Path]:
    """Every config file OpenCode could be reading, in its own precedence.

    XDG first, then the Windows AppData location (only consulted on
    Windows, mirroring what the two previous copies did). Callers that need
    backups or revert discovery use the whole list; callers that write use
    :func:`opencode_config_path`.
    """
    resolved = resolve_home(home)
    dirs = [resolved / ".config" / "opencode"]
    if sys.platform == "win32":
        appdata = Path(os.environ.get("APPDATA") or (resolved / "AppData" / "Roaming"))
        dirs.append(appdata / "opencode")
    return [d / name for d in dirs for name in OPENCODE_CONFIG_NAMES]


def opencode_config_path(home: Path | None = None) -> Path:
    """The OpenCode config file actually in effect: the first candidate that
    exists, else the default a fresh install would create."""
    for candidate in opencode_config_candidates(home):
        if candidate.is_file():
            return candidate
    return opencode_config_dir(home) / OPENCODE_CONFIG_NAMES[0]


def opencode_agents_file(home: Path | None = None) -> Path:
    """The global instructions file OpenCode V2 actually loads.

    V2 accepts the `instructions` config array but does not resolve its
    files (official docs, verified 2026-09-22): the scope file is what the
    model receives. The engine symlinks it at the canonical bootstrap, the
    same way Codex and Antigravity already work.
    """
    return opencode_config_dir(home) / "AGENTS.md"


def opencode_skills_dir(home: Path | None = None) -> Path:
    """The native V2 per-CLI skill directory OpenCode discovers."""
    return opencode_config_dir(home) / "skills"
