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

#: Where the engine's own Python environment lives, relative to the home. Like
#: the state, it stays out of the engine checkout: a venv inside it would be an
#: untracked directory, and the updater refuses to work on a dirty tree.
RUNTIME_SUBPATH = (".local", "share", "nexgen-engine", "runtime")

#: Subfolder of the engine clone that contains scripts/ and agent-universal-layer/.
ENGINE_SUBDIR = "03-INFRA"

#: Where an installed wheel keeps the same content as `03-INFRA` (the layer:
#: Council, lazy-mcp, hooks, manifests, skills, templates), inside `nexgen_core`.
BUNDLED_ENGINE_DIRNAME = "_engine"


def _resolve_home_impl(home: Path | None = None) -> Path:
    """Implementation behind :func:`resolve_home`."""
    if home is not None:
        return Path(home)
    sandbox = os.environ.get("NEXGEN_HOME")
    if sandbox:
        expanded = Path(sandbox).expanduser()
        if not expanded.is_absolute():
            # A relative sandbox anchors at the process CWD: cron and the
            # terminal would silently operate on two different homes.
            raise ValueError(
                f"NEXGEN_HOME must be an absolute path, got {sandbox!r}"
            )
        return expanded
    return Path.home()


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
    return _resolve_home_impl(home)


def _resolve_vault_data_impl(home: Path | None = None, override: Path | None = None) -> Path:
    """Implementation behind :func:`resolve_vault_data`."""
    if override is not None:
        return Path(override).expanduser()
    env = os.environ.get("AGENT_VAULT_DATA") or os.environ.get("KNOWLEDGE_VAULT_PATH")
    if env:
        return Path(env).expanduser()
    return resolve_home(home) / DEFAULT_VAULT_DIRNAME


def resolve_vault_data(home: Path | None = None, override: Path | None = None) -> Path:
    """Where the private data (the Vault) lives.

    Precedence: explicit argument, `AGENT_VAULT_DATA`, `KNOWLEDGE_VAULT_PATH`,
    finally `~/KnowledgeVault`. Environment values honor `~` like the
    shell does: without expansion the engine would work on a literal
    `./~/KnowledgeVault` and report an empty vault as if the data vanished.
    """
    return _resolve_vault_data_impl(home, override)


def _resolve_engine_root_impl(home: Path | None = None, override: Path | None = None) -> Path:
    """Implementation behind :func:`resolve_engine_root`."""
    if override is not None:
        return Path(override).expanduser()
    env = os.environ.get("AGENT_ENGINE_ROOT")
    if env:
        return Path(env).expanduser()
    default = resolve_home(home) / ENGINE_DIRNAME / ENGINE_SUBDIR
    if default.is_dir():
        return default
    # No checkout: an installed wheel carries its own copy of the layer. Without
    # this, `nexgen council` and everything else that reads the layer looked for a
    # folder that only exists in a clone.
    return bundled_engine_root() or default


def resolve_engine_root(home: Path | None = None, override: Path | None = None) -> Path:
    """Where the installed engine lives (the `03-INFRA` folder of its clone).

    Precedence: explicit argument, `AGENT_ENGINE_ROOT`, finally
    `~/.nexgen-engine/03-INFRA`.
    """
    return _resolve_engine_root_impl(home, override)


def _bundled_engine_root_impl() -> Path | None:
    """Implementation behind :func:`bundled_engine_root`."""
    candidate = Path(__file__).resolve().parent / BUNDLED_ENGINE_DIRNAME
    return candidate if (candidate / "agent-universal-layer").is_dir() else None


def bundled_engine_root() -> Path | None:
    """The engine root shipped inside an installed wheel, when there is one."""
    return _bundled_engine_root_impl()


def _installed_as_package_impl() -> bool:
    """Implementation behind :func:`installed_as_package`."""
    return bool({part.lower() for part in Path(__file__).resolve().parts} & {"site-packages", "dist-packages"})


def installed_as_package() -> bool:
    """True when this code runs from a package manager's site-packages.

    Such an install owns its launchers (pipx and uv put them in `~/.local/bin`),
    has no checkout to update with git and no pyproject to provision from. An
    editable install runs from the repository and counts as a checkout.
    """
    return _installed_as_package_impl()


def _resolve_runtime_dir_impl(home: Path | None = None) -> Path:
    """Implementation behind :func:`resolve_runtime_dir`."""
    explicit = os.environ.get("NEXGEN_RUNTIME_DIR")
    if explicit and Path(explicit).expanduser().is_absolute():
        return Path(explicit).expanduser()
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg and not os.environ.get("NEXGEN_HOME") and (home is None or Path(home) == Path.home()):
        candidate = Path(xdg).expanduser()
        if candidate.is_absolute():
            return candidate / "nexgen-engine" / "runtime"
    return resolve_home(home).joinpath(*RUNTIME_SUBPATH)


def resolve_runtime_dir(home: Path | None = None) -> Path:
    """Where the engine's own Python environment lives (see nexgen_core.runtime).

    Precedence: `NEXGEN_RUNTIME_DIR` (absolute), then `XDG_DATA_HOME` when the
    home in play is the real one, then `~/.local/share/nexgen-engine/runtime`.
    A sandbox home keeps its own, so a development checkout never shares one
    with the machine's working installation.
    """
    return _resolve_runtime_dir_impl(home)


def _resolve_state_dir_impl(home: Path | None = None, override: Path | None = None) -> Path:
    """Implementation behind :func:`resolve_state_dir`."""
    if override is not None:
        return Path(override).expanduser()
    env = os.environ.get("AGENT_STATE_DIR")
    if env:
        return Path(env).expanduser()
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg and not os.environ.get("NEXGEN_HOME") and (home is None or Path(home) == Path.home()):
        candidate = Path(xdg).expanduser()
        if candidate.is_absolute():
            return candidate
    return resolve_home(home).joinpath(*STATE_SUBPATH)


def resolve_state_dir(home: Path | None = None, override: Path | None = None) -> Path:
    """Where the machine-local state lives (locks, timestamps, debounce).

    Precedence: explicit argument, `AGENT_STATE_DIR`, `XDG_STATE_HOME`,
    finally `~/.local/state`. `XDG_STATE_HOME` applies when the home in play is
    the real one, whether the caller left it implicit or passed it along: a
    sandbox `home` or `NEXGEN_HOME` keeps state under that home. Telling the
    two apart by "was a home passed" instead made the guard and `HostLock()`
    resolve different lock files on a machine with XDG_STATE_HOME set, so two
    writers each believed they were alone. A relative value is ignored, as the
    XDG specification requires: honoring it anchors state at the working
    directory.
    """
    return _resolve_state_dir_impl(home, override)


def _canonical_instructions_impl(vault_data: Path | None = None) -> Path:
    """Implementation behind :func:`canonical_instructions`."""
    root = vault_data if vault_data is not None else resolve_vault_data()
    return Path(root) / ENGINE_SUBDIR / "agent-universal-layer" / "instructions" / "AGENTS.md"


def canonical_instructions(vault_data: Path | None = None) -> Path:
    """The canonical instructions file from which every runtime derives its own."""
    return _canonical_instructions_impl(vault_data)


def _mcp_manifest_impl(vault_data: Path | None = None) -> Path:
    """Implementation behind :func:`mcp_manifest`."""
    root = vault_data if vault_data is not None else resolve_vault_data()
    return Path(root) / ENGINE_SUBDIR / "agent-universal-layer" / "mcp" / "manifest.yaml"


def mcp_manifest(vault_data: Path | None = None) -> Path:
    """The connector manifest, the single source of the MCP configuration."""
    return _mcp_manifest_impl(vault_data)


def _skills_manifest_impl(vault_data: Path | None = None) -> Path:
    """Implementation behind :func:`skills_manifest`."""
    root = vault_data if vault_data is not None else resolve_vault_data()
    return Path(root) / ENGINE_SUBDIR / "agent-universal-layer" / "skills" / "skills.manifest.yaml"


def skills_manifest(vault_data: Path | None = None) -> Path:
    """The skills manifest, the single source of what gets materialized."""
    return _skills_manifest_impl(vault_data)


def _remotes_config_impl(vault_data: Path | None = None) -> Path:
    """Implementation behind :func:`remotes_config`."""
    root = vault_data if vault_data is not None else resolve_vault_data()
    return Path(root) / ENGINE_SUBDIR / "agent-universal-layer" / "sync" / "remotes.yaml"


def remotes_config(vault_data: Path | None = None) -> Path:
    """The declaration of the authoritative remote and its mirrors."""
    return _remotes_config_impl(vault_data)


#: File names OpenCode itself resolves, in the order it prefers them. The
#: release used to resolve an EXISTING file with priority jsonc > json >
#: config.json; that precedence is a contract (an already-configured machine
#: must be updated on the file it actually reads, never on a fresh copy next
#: to it) and now lives here instead of in two copies that could diverge
#: (renderer MCP vs guardrail adapter).
OPENCODE_CONFIG_NAMES: tuple[str, ...] = ("opencode.jsonc", "opencode.json", "config.json")


def _opencode_config_dir_impl(home: Path | None = None) -> Path:
    """Implementation behind :func:`opencode_config_dir`."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg).expanduser() / "opencode"
    return resolve_home(home) / ".config" / "opencode"


def opencode_config_dir(home: Path | None = None) -> Path:
    """The directory holding OpenCode's global configuration.

    Honors `XDG_CONFIG_HOME` like OpenCode itself does: without it the
    engine reads and writes `~/.config/opencode` while the CLI on an
    XDG-redirected machine reads elsewhere, and every render, backup and
    revert operates on the wrong file while reporting success.
    """
    return _opencode_config_dir_impl(home)


def _opencode_config_candidates_impl(home: Path | None = None) -> list[Path]:
    """Implementation behind :func:`opencode_config_candidates`."""
    resolved = resolve_home(home)
    dirs = [opencode_config_dir(home)]
    if sys.platform == "win32":
        appdata = Path(os.environ.get("APPDATA") or (resolved / "AppData" / "Roaming"))
        candidate = appdata / "opencode"
        if candidate != dirs[0]:
            dirs.append(candidate)
    return [d / name for name in OPENCODE_CONFIG_NAMES for d in dirs]


def opencode_config_candidates(home: Path | None = None) -> list[Path]:
    """Every config file OpenCode could be reading, in its own precedence.

    Name-major across directories (`opencode.jsonc` anywhere beats
    `config.json` anywhere): that is the declared contract above, and a
    dir-major order would let a stale `config.json` shadow the live
    `opencode.jsonc` on multi-location machines. Then the Windows AppData
    location (only consulted on Windows, mirroring what the two previous
    copies did). Callers that need backups or revert discovery use the
    whole list; callers that write use :func:`opencode_config_path`.
    """
    return _opencode_config_candidates_impl(home)


def _opencode_config_path_impl(home: Path | None = None) -> Path:
    """Implementation behind :func:`opencode_config_path`."""
    for candidate in opencode_config_candidates(home):
        if candidate.is_file():
            return candidate
    return opencode_config_dir(home) / OPENCODE_CONFIG_NAMES[0]


def opencode_config_path(home: Path | None = None) -> Path:
    """The OpenCode config file actually in effect: the first candidate that
    exists, else the default a fresh install would create."""
    return _opencode_config_path_impl(home)


def _opencode_agents_file_impl(home: Path | None = None) -> Path:
    """Implementation behind :func:`opencode_agents_file`."""
    return opencode_config_dir(home) / "AGENTS.md"


def opencode_agents_file(home: Path | None = None) -> Path:
    """The global instructions file OpenCode V2 actually loads.

    V2 accepts the `instructions` config array but does not resolve its
    files (official docs, verified 2026-09-22): the scope file is what the
    model receives. The engine symlinks it at the canonical bootstrap, the
    same way Codex and Antigravity already work.
    """
    return _opencode_agents_file_impl(home)


def _opencode_skills_dir_impl(home: Path | None = None) -> Path:
    """Implementation behind :func:`opencode_skills_dir`."""
    return opencode_config_dir(home) / "skills"


def opencode_skills_dir(home: Path | None = None) -> Path:
    """The native V2 per-CLI skill directory OpenCode discovers."""
    return _opencode_skills_dir_impl(home)


def _claude_config_impl(home: Path | None = None) -> Path:
    """Implementation behind :func:`claude_config`."""
    return resolve_home(home) / ".claude.json"


def claude_config(home: Path | None = None) -> Path:
    """Claude Code's MCP configuration (session/trust state lives here too,
    which is why only additive writes are safe on it)."""
    return _claude_config_impl(home)


def _claude_settings_impl(home: Path | None = None) -> Path:
    """Implementation behind :func:`claude_settings`."""
    return resolve_home(home) / ".claude" / "settings.json"


def claude_settings(home: Path | None = None) -> Path:
    """Claude Code's posture/hook settings (written only by Claude itself
    on first launch -- never a valid installed-signal, see the runtimes)."""
    return _claude_settings_impl(home)


def _claude_desktop_config_impl(home: Path | None = None) -> Path:
    """Implementation behind :func:`claude_desktop_config`."""
    resolved = resolve_home(home)
    if sys.platform == "win32":
        appdata = Path(os.environ.get("APPDATA") or (resolved / "AppData" / "Roaming"))
        return appdata / "Claude" / "claude_desktop_config.json"
    if sys.platform == "darwin":
        return resolved / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg).expanduser() / "Claude" / "claude_desktop_config.json"
    return resolved / ".config" / "Claude" / "claude_desktop_config.json"


def claude_desktop_config(home: Path | None = None) -> Path:
    """Claude Desktop's MCP configuration (the Electron app, not the CLI).

    The app owns everything else in this file (preferences, window state):
    the renderer may only touch its `mcpServers` table, additively, and
    never while the app is running (it rewrites the file on save).
    """
    return _claude_desktop_config_impl(home)


def _codex_home_impl(home: Path | None = None) -> Path:
    """Implementation behind :func:`codex_home`."""
    override = os.environ.get("CODEX_HOME")
    if override:
        return Path(override).expanduser()
    return resolve_home(home) / ".codex"


def codex_home(home: Path | None = None) -> Path:
    """Codex's home: `$CODEX_HOME` wins when set, else `~/.codex` under `home`.

    The environment keeps precedence on purpose: every in-repo caller
    passes the effective home, so `$CODEX_HOME` custom locations keep
    working. Sandboxes needing isolation must unset the variable instead
    of passing a different home and expecting it to win.
    """
    return _codex_home_impl(home)


def _codex_config_impl(home: Path | None = None) -> Path:
    """Implementation behind :func:`codex_config`."""
    return codex_home(home) / "config.toml"


def codex_config(home: Path | None = None) -> Path:
    """Codex's native MCP configuration (TOML)."""
    return _codex_config_impl(home)


#: Folders Antigravity reads an MCP config from. The renderer owns exactly
#: one of them (the canonical file below) and fans it out to the rest as
#: symlinks, because different ways of launching Antigravity have been
#: observed reading different folders.
ANTIGRAVITY_CONSUMER_DIRS: tuple[str, ...] = ("antigravity-cli", "antigravity-ide", "config")


def _antigravity_config_impl(home: Path | None = None) -> Path:
    """Implementation behind :func:`antigravity_config`."""
    return resolve_home(home) / ".gemini" / "antigravity" / "mcp_config.json"


def antigravity_config(home: Path | None = None) -> Path:
    """The canonical Antigravity MCP configuration the renderer writes.

    Note this is `antigravity/mcp_config.json`, NOT one of the fan-out
    copies: revert/reset must operate here, where the backups live. Reads
    may go through any fan-out (same content), writes never do.
    """
    return _antigravity_config_impl(home)


def _antigravity_configs_impl(home: Path | None = None) -> list[Path]:
    """Implementation behind :func:`antigravity_configs`."""
    resolved = resolve_home(home)
    return [antigravity_config(resolved)] + [
        resolved / ".gemini" / directory / "mcp_config.json"
        for directory in ANTIGRAVITY_CONSUMER_DIRS
    ]


def antigravity_configs(home: Path | None = None) -> list[Path]:
    """Canonical file first, then every fan-out copy (backup discovery)."""
    return _antigravity_configs_impl(home)


def _openclaw_config_impl(home: Path | None = None) -> Path:
    """Implementation behind :func:`openclaw_config`."""
    explicit = os.environ.get("OPENCLAW_CONFIG_PATH")
    if explicit:
        return Path(explicit).expanduser()
    state_dir = os.environ.get("OPENCLAW_STATE_DIR")
    if state_dir:
        return Path(state_dir).expanduser() / "openclaw.json"
    return resolve_home(home) / ".openclaw" / "openclaw.json"


def openclaw_config(home: Path | None = None) -> Path:
    """OpenClaw's own configuration file, which also carries its MCP servers.

    Precedence follows OpenClaw itself: `$OPENCLAW_CONFIG_PATH` (a file),
    then `$OPENCLAW_STATE_DIR/openclaw.json`, finally `~/.openclaw/openclaw.json`.
    The file holds everything OpenClaw knows (models, channels, gateway
    settings), so the renderer only ever touches the `mcp.servers` table.
    """
    return _openclaw_config_impl(home)


def _antigravity_settings_impl(home: Path | None = None) -> Path:
    """Implementation behind :func:`antigravity_settings`."""
    return resolve_home(home) / ".gemini" / "antigravity-cli" / "settings.json"


def antigravity_settings(home: Path | None = None) -> Path:
    """Antigravity's posture/hook settings."""
    return _antigravity_settings_impl(home)


def _antigravity_hooks_impl(home: Path | None = None) -> Path:
    """Implementation behind :func:`antigravity_hooks`."""
    return resolve_home(home) / ".gemini" / "config" / "hooks.json"


def antigravity_hooks(home: Path | None = None) -> Path:
    """Antigravity's event-sink hook registration file."""
    return _antigravity_hooks_impl(home)
