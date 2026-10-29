"""What the local Antigravity backend needs before OpenClaw can run an agent on it.

One job: say, from the pin file and from this machine's own files, whether the
`google-antigravity-cli` plugin is installed at the exact release the engine
pins, whether the `agy` command it drives is on PATH, whether a login is
visible, and which agent folders `agy` has not been trusted with yet.

Nothing here installs, writes or launches anything: the setup step that acts on
these facts is `setup_agy`, and the doctor only reports them. The login is the
one question this machine cannot answer by itself: `agy` keeps the credential in
the system keyring (the Windows Credential Manager holds `gemini:antigravity`),
so an absent credential file means "not verifiable", never "not logged in". A
doctor that said "log in" to a person who already is would be the same
misleading line this module exists to remove.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from nexgen_core.openclaw import pin
from nexgen_core.openclaw.terms import LOCAL_AGY_BACKEND

#: The pin file beside OpenClaw's own: a third-party plugin, so an exact release.
PIN_FILE = "agy-plugin.json"

#: Where a login would be a file the engine can see without opening it.
#: `agy` itself keeps the credential in the keyring, so this is the Gemini CLI's
#: shape, shared when both clients are installed; its absence proves nothing.
CREDENTIAL_FILES: tuple[Path, ...] = (Path(".gemini") / "oauth_creds.json",)

#: `agy`'s own settings, where it records the folders it was trusted with.
SETTINGS_RELATIVE = Path(".gemini") / "antigravity-cli" / "settings.json"
#: The key inside that file: absolute folder paths, no secrets.
TRUST_KEY = "trustedWorkspaces"


def pin_path(engine_root: Path | None = None) -> Path:
    """The plugin's pin, looked for beside each place OpenClaw's own pin may be."""
    candidates = [candidate.with_name(PIN_FILE) for candidate in pin.pin_candidates(engine_root)]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def plugin_pin(engine_root: Path | None = None) -> dict[str, str] | None:
    """The npm package and the exact version to install, or None when unreadable.

    A pin this module cannot read is never replaced by a version compiled into
    the code: the setup refuses to install an unknown release, exactly as it does
    for OpenClaw itself.
    """
    try:
        loaded = json.loads(pin_path(engine_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):  # nexgen: allow reason="a missing or broken pin is reported as an unknown release, never as a crash"
        return None
    if not isinstance(loaded, dict):
        return None
    package = loaded.get("package")
    version = loaded.get("version")
    if not (isinstance(package, str) and package.strip() and isinstance(version, str) and version.strip()):
        return None
    return {"package": package.strip(), "version": version.strip()}


def install_spec(plugin: dict[str, str]) -> str:
    """The npm spec OpenClaw is given: the `npm:` form, so a bundled id cannot win."""
    return f"npm:{plugin['package']}@{plugin['version']}"


def install_argv(openclaw_bin: str, plugin: dict[str, str]) -> list[str]:
    """The one command that puts the plugin on this gateway at the pinned release.

    `--force` is what a non-interactive install of an arbitrary npm package needs,
    and it is also what replaces an existing copy at another version.
    `--no-enable` keeps activation where it belongs: the engine owns
    `openclaw.json` and writes the allow-list and the entry itself, so the
    install must not decide which plugin is on.
    """
    return [openclaw_bin, "plugins", "install", install_spec(plugin), "--force", "--no-enable"]


def extension_dir(state_dir: Path, backend: str = LOCAL_AGY_BACKEND) -> Path:
    """Where OpenClaw keeps an installed plugin: `<state>/extensions/<id>`."""
    return Path(state_dir) / "extensions" / backend


def installed_version(state_dir: Path, backend: str = LOCAL_AGY_BACKEND) -> str | None:
    """The release on disk, read from the plugin's own `package.json`, or None."""
    manifest = extension_dir(state_dir, backend) / "package.json"
    try:
        loaded = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # nexgen: allow reason="an unreadable manifest means the plugin is not installed at a version this engine can name"
        return None
    version = loaded.get("version") if isinstance(loaded, dict) else None
    return version.strip() if isinstance(version, str) and version.strip() else None


def _names_backend(model: Any) -> bool:
    """Whether a model ref selects the local backend by its provider prefix."""
    return isinstance(model, str) and model.split("/", 1)[0].strip().lower() == LOCAL_AGY_BACKEND


def _runtime(spec: Any) -> str:
    """The `agentRuntime.id` a model entry declares, lowercased, or an empty string."""
    runtime = spec.get("agentRuntime") if isinstance(spec, dict) else None
    value = runtime.get("id") if isinstance(runtime, dict) else None
    return str(value).strip().lower() if isinstance(value, str) and value.strip() else ""


def requested(squad: dict[str, Any] | None) -> bool:
    """Whether the squad puts an agent, or a model, on the local `agy` CLI.

    Both shapes OpenClaw accepts are read: the provider prefix of a model ref and
    an `agentRuntime.id` naming the backend, because a squad may declare either.
    """
    if not isinstance(squad, dict):
        return False
    agents = squad.get("agents")
    for spec in agents.values() if isinstance(agents, dict) else ():
        if isinstance(spec, dict) and _names_backend(spec.get("model")):
            return True
    models = squad.get("models")
    for name, spec in models.items() if isinstance(models, dict) else ():
        if _names_backend(name) or _runtime(spec) == LOCAL_AGY_BACKEND:
            return True
    return False


def login_file(home: Path) -> Path | None:
    """The credential file a login would leave where the engine can see it, or None.

    The file is only tested for existence and never opened: the credential inside
    is a secret and stays one. None means "this machine cannot tell", which is
    what the doctor says instead of a false "log in".
    """
    for relative in CREDENTIAL_FILES:
        candidate = Path(home) / relative
        if candidate.exists():
            return candidate
    return None


def settings_path(home: Path) -> Path:
    """`agy`'s own settings file, the one that holds the trusted folders."""
    return Path(home) / SETTINGS_RELATIVE


def trusted_workspaces(home: Path) -> tuple[str, ...]:
    """The folders `agy` was trusted with, as it recorded them; empty when unreadable."""
    try:
        loaded = json.loads(settings_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):  # nexgen: allow reason="a settings file this engine cannot read says nothing about trust, and a missing one means nothing was trusted yet"
        return ()
    values = loaded.get(TRUST_KEY) if isinstance(loaded, dict) else None
    if not isinstance(values, list):
        return ()
    return tuple(str(item) for item in values if isinstance(item, str) and item.strip())


def _comparable(path: str) -> str:
    """A path two writers can agree on: normalised, and case-folded where the system does."""
    return os.path.normcase(os.path.normpath(path.strip()))


def untrusted(workspaces: list[str], trusted: tuple[str, ...]) -> list[str]:
    """The agent folders `agy` would ask about again, in the order given."""
    known = {_comparable(item) for item in trusted}
    return [folder for folder in workspaces if folder.strip() and _comparable(folder) not in known]


def agent_workspaces(config: dict[str, Any] | None) -> list[str]:
    """Every folder an agent of this configuration works in, without duplicates."""
    agents = (config or {}).get("agents")
    entries = agents.get("entries") if isinstance(agents, dict) else None
    found: list[str] = []
    for entry in entries.values() if isinstance(entries, dict) else ():
        workspace = entry.get("workspace") if isinstance(entry, dict) else None
        if isinstance(workspace, str) and workspace.strip() and workspace not in found:
            found.append(workspace)
    return found
