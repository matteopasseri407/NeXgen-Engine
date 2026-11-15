"""Claude Code as a native plugin: one generated directory instead of three edits.

One job: render what the engine already knows how to produce for Claude Code -- the
guardrail hook, the MCP servers, the skill views -- as ONE plugin directory under the
engine's state folder, and hand it to Claude Code's own installer (`claude plugin
validate`, `claude plugin marketplace add`, `claude plugin install`).

Why it exists. Each CLI's configuration format used to be private, so the engine edited
the person's own files: `~/.claude.json` for the servers, `~/.claude/settings.json` for the
hook, `~/.claude/skills` for the views, and most of `runtimes/claude.py` is the repair work
that editing someone else's file needs. Claude Code now loads a plugin directory,
namespaces what it finds there and validates it before it runs, so the same three
artifacts can be installed once and read from one place. The switch is opt-in and off by
default (`plugins.claude.enabled` in the installation's private config): while it is on
the three classic writers stand down, so nothing is registered twice, and while it is off
this module writes nothing at all.

What stays the engine's: which body the guardrail runs, which servers the manifest
mounts, which skills exist, and the refusal to install a directory Claude Code rejects.
What moves out: merging into the person's files, repairing a stale registration, and the
plugin schema itself -- `claude plugin validate` owns that, and this module installs
nothing it has not first asked that command about.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Sequence

from nexgen_core import __version__
from nexgen_core.action_notes import WARN
from nexgen_core.files import atomic_write_bytes, atomic_write_text
from nexgen_core.i18n import t

#: The plugin and the marketplace the engine registers: a person installs
#: `nexgen@nexgen-engine`.
PLUGIN_NAME = "nexgen"
MARKETPLACE_NAME = "nexgen-engine"
DISPLAY_NAME = "NeXgen Engine"

#: The opt-in switch, read from the private config the other installation switches live in.
SWITCH = ("plugins", "claude", "enabled")

#: The digest of the tree the installer last accepted. Beside the plugin, never inside it:
#: whatever the plugin directory carries becomes part of the plugin.
MARKER_NAME = ".nexgen-install.json"

#: One `claude plugin …` call. The installer copies files and writes the person's own
#: settings, so it is given room; a stuck call must not hold the whole guard cycle.
COMMAND_TIMEOUT_SECONDS = 120

#: Every `${NAME}` a server entry leaves for its process environment. The plugin promises
#: the value from the credential store instead, so the promise is declared as a
#: `userConfig` option and the reference is rewritten to `${user_config.NAME}`.
_ENV_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

Run = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]


def enabled(home: Path | None = None, vault_data: Path | None = None) -> bool:
    """Whether this installation asked for the Claude Code plugin. Off unless it says so.

    A private config that cannot be read means "off": the switch only ever adds a second
    way of writing the same three artifacts, so an unreadable file must leave the classic
    writers doing their job instead of leaving Claude with neither. `vault_data` is passed
    by callers that already resolved it, so the answer is read from the vault they write to.
    """
    from nexgen_core.cli.remotes_file import RemotesFileError, read_remotes_file
    from nexgen_core.paths import remotes_config, resolve_vault_data

    try:
        data = read_remotes_file(remotes_config(resolve_vault_data(home, vault_data)))
    except RemotesFileError:  # nexgen: allow reason="an unreadable private config means the opt-in switch is off; the guard cycle already reports that file"
        return False
    node: Any = data
    for key in SWITCH:
        if not isinstance(node, dict):
            return False
        node = node.get(key)
    return node is True


def marketplace_dir(state_dir: Path) -> Path:
    """The directory Claude Code registers as a marketplace: the plugin lives under it."""
    return Path(state_dir) / "plugins" / "claude"


def installed(home: Path | None = None, vault_data: Path | None = None, state_dir: Path | None = None) -> bool:
    """Whether the switch is on AND Claude Code accepted the tree the engine generated.

    The three classic writers stand down on this, never on the switch alone: a machine that
    asked for the plugin while the install keeps failing keeps the hook, the servers and the
    views it already had, so an opt-in can never leave Claude Code unguarded. The marker
    this reads is written only after `claude plugin install` succeeded.
    """
    if not enabled(home, vault_data):
        return False
    from nexgen_core.paths import resolve_state_dir

    return (marketplace_dir(state_dir if state_dir is not None else resolve_state_dir(home)) / MARKER_NAME).is_file()


def apply(
    *,
    vault_data: Path,
    engine_root: Path,
    home: Path,
    state_dir: Path,
    run: Run | None = None,
    which: Callable[[str], str | None] | None = None,
) -> list[str]:
    """The guard-cycle step: render the plugin, then install it when it actually moved.

    Silent in both cases where it has nothing to say: the switch is off (the classic
    writers keep working), and the directory is byte-identical to the one the installer
    already accepted. `run` and `which` are the seams a test replaces; the real ones are
    `subprocess.run` and `shutil.which`.
    """
    if not enabled(home, vault_data):
        return []
    root = marketplace_dir(state_dir)
    files = _plugin_files(vault_data=vault_data, engine_root=engine_root, home=home, root=root)
    _write_tree(root, files)
    digest = _digest(files)
    marker = root / MARKER_NAME
    if _digest_of_marker(marker) == digest:
        return []
    exe = (which or shutil.which)("claude")
    if not exe:
        # Claude Code is not installed here: the directory is ready for the day it is, and
        # a warning every half hour for a CLI the person does not run would be noise.
        return []
    ok, detail = _install(root, exe, run or _run_claude)
    if not ok:
        return [WARN + t("Claude plugin not installed: {detail}", detail=detail)]
    atomic_write_text(marker, json.dumps({"digest": digest, "plugin": _plugin_id()}, indent=2) + "\n")
    return [t("Claude Code plugin rendered in {path} and installed through `claude plugin`", path=root)]


def _plugin_id() -> str:
    """How Claude Code names an installed plugin: `plugin@marketplace`."""
    return f"{PLUGIN_NAME}@{MARKETPLACE_NAME}"


def _run_claude(argv: Sequence[str]) -> "subprocess.CompletedProcess[str]":
    """One `claude` call: windowless, bounded, UTF-8 whatever the console is."""
    from nexgen_core.processes import no_window_kwargs, windows_command_argv

    return subprocess.run(
        windows_command_argv(list(argv)),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=COMMAND_TIMEOUT_SECONDS,
        **no_window_kwargs(),
    )


def _install(root: Path, exe: str, run: Run) -> tuple[bool, str]:
    """Validate, register the marketplace, install -- each step gates the next.

    The validator is the authority on whether Claude Code would load this directory, so a
    refused directory never reaches the installer. Both remaining commands are the
    vendor's own: the engine decides what goes in the plugin, Claude Code decides how it
    is installed.
    """
    validated = _call([exe, "plugin", "validate", str(root), "--json"], run, "validate")
    if isinstance(validated, str):
        return False, validated
    if validated.returncode != 0:
        return False, t("`claude plugin validate` refused {path}: {output}", path=root, output=_tail(validated))
    registered = _call([exe, "plugin", "marketplace", "add", str(root), "--scope", "user"], run, "marketplace add")
    if isinstance(registered, str):
        return False, registered
    if registered.returncode != 0:
        return False, t("`claude plugin marketplace add` failed: {output}", output=_tail(registered))
    installed = _call([exe, "plugin", "install", _plugin_id(), "--scope", "user", "--json"], run, "install")
    if isinstance(installed, str):
        return False, installed
    if installed.returncode != 0:
        return False, t("`claude plugin install` failed: {output}", output=_tail(installed))
    return True, ""


def _call(argv: Sequence[str], run: Run, verb: str) -> "subprocess.CompletedProcess[str] | str":
    """One CLI call, or the sentence that says why it could not be made."""
    try:
        return run(argv)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return t("`claude plugin {verb}` could not run ({error})", verb=verb, error=exc)


def _tail(done: "subprocess.CompletedProcess[str]", limit: int = 400) -> str:
    """The end of what the CLI said: the part that names the problem, without the whole log."""
    text = " ".join(f"{done.stdout or ''} {done.stderr or ''}".split())
    return text[-limit:] if len(text) > limit else text


# --------------------------------------------------------------- the generated tree


def _plugin_files(*, vault_data: Path, engine_root: Path, home: Path, root: Path) -> dict[str, bytes]:
    """Every file of the generated marketplace, keyed by its path from the marketplace root.

    Built whole in memory before anything is written, so a read that fails leaves the
    previous tree -- and the install it belongs to -- exactly as it was.
    """
    servers, options = _mcp(vault_data=vault_data, engine_root=engine_root, home=home)
    files: dict[str, bytes] = {}
    files[f"{PLUGIN_NAME}/.mcp.json"] = _json({"mcpServers": servers})
    files[f"{PLUGIN_NAME}/.claude-plugin/plugin.json"] = _json(_manifest(options))
    files.update(_hook_files(vault_data=vault_data, engine_root=engine_root, home=home, root=root))
    files.update(_skill_files(vault_data=vault_data, engine_root=engine_root, home=home))
    files[".claude-plugin/marketplace.json"] = _json(_marketplace_catalog())
    return files


def _manifest(options: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """`.claude-plugin/plugin.json`: what the plugin is, and the values it must ask for."""
    manifest: dict[str, Any] = {
        "name": PLUGIN_NAME,
        "displayName": DISPLAY_NAME,
        "version": __version__ if __version__ != "unknown" else "0.0.0",
        "description": (
            "NeXgen Engine: the guardrail hook, the MCP connectors and the skills of this "
            "installation, installed as one plugin instead of written into your own files."
        ),
        "author": {"name": DISPLAY_NAME},
    }
    if options:
        manifest["userConfig"] = options
    return manifest


def _marketplace_catalog() -> dict[str, Any]:
    """`.claude-plugin/marketplace.json`: the one-entry catalog `claude plugin install` reads."""
    return {
        "name": MARKETPLACE_NAME,
        "owner": {"name": DISPLAY_NAME},
        "description": "The plugin this NeXgen installation generates from its own manifest.",
        "plugins": [
            {
                "name": PLUGIN_NAME,
                "source": f"./{PLUGIN_NAME}",
                "description": "Guardrail hook, MCP connectors and skills from the engine manifest.",
            }
        ],
    }


def _mcp(*, vault_data: Path, engine_root: Path, home: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """The servers Claude Code mounts, and the sensitive options their credentials come from."""
    from nexgen_core.mcp_render.claude import claude_mcp_entries
    from nexgen_core.renderer import McpRenderer

    renderer = McpRenderer(vault_data=vault_data, engine_root=engine_root, home=home)
    servers = renderer.load_resolved_servers("claude")
    entries = claude_mcp_entries(renderer, servers)
    wanted: dict[str, str] = {}
    for name, entry in entries.items():
        for variable in _references(entry):
            wanted.setdefault(variable, name)
        _reference_user_config(entry)
    options = {
        variable: {
            "type": "string",
            "title": variable,
            "description": t(
                "{server} needs this value to start. Claude Code keeps it in this system's "
                "credential store instead of a configuration file.",
                server=server,
            ),
            "sensitive": True,
            "required": True,
        }
        for variable, server in wanted.items()
    }
    return entries, options


def _references(entry: dict[str, Any]) -> list[str]:
    """Every `${NAME}` left in one server entry: the values it would read from its environment."""
    values: list[str] = []
    for field in ("env", "headers"):
        held = entry.get(field)
        if isinstance(held, dict):
            values.extend(str(value) for value in held.values())
    return [match.group(1) for value in values for match in _ENV_REFERENCE.finditer(value)]


def _reference_user_config(entry: dict[str, Any]) -> None:
    """Points one server entry at the plugin's own options instead of the process environment."""
    for field in ("env", "headers"):
        held = entry.get(field)
        if isinstance(held, dict):
            entry[field] = {
                key: _ENV_REFERENCE.sub(r"${user_config.\1}", str(value)) for key, value in held.items()
            }


def _hook_files(*, vault_data: Path, engine_root: Path, home: Path, root: Path) -> dict[str, bytes]:
    """The guardrail: the body from the private manifest, the fail-closed adapter and its sidecar.

    No declared body means no hook, which is the answer the classic adapter gives too: the
    guardrail policy is Vault-private data and must never be inherited by another install.
    The refusals that explain a missing body belong to the classic phase, which reads the
    same manifest and reports them for every CLI.
    """
    from nexgen_core.guard_permissions import guardrail_body
    from nexgen_core.runtimes.base import GuardrailError, Runtime, sidecar_content
    from nexgen_core.runtimes.claude import BODY_TIMEOUT_SECONDS, adapter_name, plugin_hook_group

    body, _refusals = guardrail_body(vault_data)
    if body is None:
        return {}
    if not Runtime._is_safe_hook_filename(body.name):
        raise GuardrailError(f"claude: unsafe guardrail filename {body.name!r}")
    directory = f"{PLUGIN_NAME}/hooks"
    sidecar = sidecar_content(
        {},
        body=root / directory / body.name,
        audit_file=Runtime.guardrail_audit_file(home, "claude"),
        timeout=BODY_TIMEOUT_SECONDS,
    )
    return {
        f"{directory}/hooks.json": _json({"hooks": {"PreToolUse": [plugin_hook_group(adapter_name())]}}),
        f"{directory}/{adapter_name()}": _engine_hook(engine_root, adapter_name()),
        f"{directory}/nexgen-guardrail-core.mjs": _engine_hook(engine_root, "nexgen-guardrail-core.mjs"),
        f"{directory}/nexgen-guardrail.config.json": sidecar.encode("utf-8"),
        f"{directory}/{body.name}": body.read_bytes(),
    }


def _engine_hook(engine_root: Path, name: str) -> bytes:
    """One script the engine ships for its hooks; a missing one is a refusal, not a silent gap."""
    from nexgen_core.runtimes.base import GuardrailError

    source = engine_root / "agent-universal-layer" / "hooks" / name
    if not source.is_file():
        raise GuardrailError(f"claude: missing engine hook ({source})")
    return source.read_bytes()


def _skill_files(*, vault_data: Path, engine_root: Path, home: Path) -> dict[str, bytes]:
    """The skills Claude Code would otherwise discover in `~/.claude/skills`, carried in the plugin.

    Read from the library, not from the manifest source: that is where the skills phase has
    already resolved every origin (vault, engine, github, installer) into one folder, and a
    plugin that read the origins itself would be a second copy of that resolution.
    """
    from nexgen_core.skills import SkillMaterializer

    materializer = SkillMaterializer(vault_data=vault_data, engine_root=engine_root, home=home)
    files: dict[str, bytes] = {}
    for name, entry in sorted(materializer.desired().items()):
        source = materializer.library_dir / name
        if "claude" not in (entry.targets or []) or not source.is_dir():
            continue
        for directory, _subdirs, names in os.walk(source):
            for filename in sorted(names):
                path = Path(directory) / filename
                relative = path.relative_to(source).as_posix()
                files[f"{PLUGIN_NAME}/skills/{name}/{relative}"] = path.read_bytes()
    return files


# ------------------------------------------------------------------- the disk mechanics


def _json(data: dict[str, Any]) -> bytes:
    """The one formatting of a generated JSON file: stable bytes make the digest meaningful."""
    return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _digest(files: dict[str, bytes]) -> str:
    """One hash over the whole generated tree: paths and bytes, order-independent."""
    hasher = hashlib.sha256()
    for path in sorted(files):
        hasher.update(path.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(hashlib.sha256(files[path]).digest())
    return hasher.hexdigest()


def _digest_of_marker(marker: Path) -> str:
    """The digest of the tree the installer last accepted, or "" when there is none."""
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # nexgen: allow reason="no marker yet is the normal first run, and an unreadable one only means the installer is asked again"
        return ""
    return str(data.get("digest") or "") if isinstance(data, dict) else ""


def _write_tree(root: Path, files: dict[str, bytes]) -> None:
    """Writes what changed and takes back what the manifest no longer declares.

    Byte-identical files are left alone: the guard runs every half hour, and rewriting them
    would change mtimes, churn the installer's cache and make the digest check meaningless.
    """
    root.mkdir(parents=True, exist_ok=True)
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file() and target.read_bytes() == content:
            continue
        atomic_write_bytes(target, content)
    _prune(root, set(files) | {MARKER_NAME})


def _prune(root: Path, keep: set[str]) -> None:
    """Removes generated files nothing declares any more, and the folders they leave empty."""
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        relative = path.relative_to(root).as_posix()
        if path.is_file() or path.is_symlink():
            if relative not in keep:
                path.unlink()
        elif path.is_dir() and not any(path.iterdir()):
            path.rmdir()
