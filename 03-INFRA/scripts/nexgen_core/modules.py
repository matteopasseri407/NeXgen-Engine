"""The module catalog: what the engine is made of, and its per-machine state.

Modules come from two places. The engine ships its own in ``modules.yaml``.
Anything else declares itself: a repository carrying ``nexgen-module.yaml`` at
its root is a module, and a machine picks it up by listing its path. That is
what makes the engine a host rather than a fixed set -- adding a module never
means editing the engine.

The engine is a set of named modules (``modules.yaml``, shipped with the
engine): memory, connectors, services, features. Each module can run on this
machine (local), on a VPS the user owns (remote), or not at all (absent).
This module owns the catalog contract and the honest derivation of what a
machine actually has: a module is active only when its env gates are set,
and its backend location comes from the per-machine state file
(``modules.state.yaml``) written by the installer.

Nothing here activates or installs anything. It is inventory and judgment,
the same split as the rest of the layer.
"""
from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import yaml

from nexgen_core.config import ConfigError, _load_yaml
from nexgen_core.i18n import t
from nexgen_core.paths import resolve_vault_data

MODULE_KINDS = ("core", "connector", "service", "feature")
MODULE_STATES = ("absent", "local", "remote")
SCHEMA_VERSIONS = (1, 2)
STATE_SCHEMA_VERSIONS = (1, 2)

# Filename a repository uses to declare itself a module.
MODULE_MANIFEST = "nexgen-module.yaml"

# Where a module's declaration applies. This belongs to the module, not to the
# person typing the command: whether a thing can run on any machine is a
# property of the thing. A voice cockpit owns a keyboard, a microphone and a
# GPU and would not run on a laptop; an MCP connector runs anywhere.
#
# External modules default to `host`, which is the safe end: a declaration
# that stays where it was made cannot switch on a module somewhere it was
# never meant to be. The engine's own catalog defaults to `shared`, because
# those are network services and that is what they have always been.
MODULE_SCOPES = ("host", "shared")

# Integrations a module can ask the CLI runtimes for. Each name maps to a
# method the runtime adapters already implement; a module never supplies code.
RUNTIME_HOOKS = ("event_sink", "guardrail")

_PROVIDES_KEYS = ("shims", "systemd_units", "runtime_hooks", "config_files", "compose_file")
_REQUIRES_KEYS = ("binaries", "devices", "groups", "paths", "gpu_mb", "setup")


@dataclass(frozen=True)
class ModuleProvides:
    """What the engine installs on a module's behalf.

    Declarative on purpose: every entry names a primitive the engine already
    owns (shims.py, scheduler.py, runtimes/), so hosting a new kind of module
    is a vocabulary change and not a new execution path.
    """

    # name -> executable it should run. A name alone would not be enough: a
    # module's command often lives in its own venv, not in its source tree.
    shims: tuple[tuple[str, str], ...] = ()
    systemd_units: tuple[str, ...] = ()
    runtime_hooks: tuple[str, ...] = ()
    # source (relative to the module) -> destination on this machine. For the
    # small user-level files a module needs dropped somewhere specific, like a
    # PipeWire fragment. Not a package manager: anything heavy belongs to the
    # module's own setup, which the engine reports rather than runs.
    config_files: tuple[tuple[str, str], ...] = ()
    # A docker-compose.yml the module ships, deployed under the engine's stack
    # root as `stack`. Without this an external module could name a `stack`
    # the engine has never heard of and get silence.
    compose_file: str = ""

    def __bool__(self) -> bool:
        return bool(
            self.shims or self.systemd_units or self.runtime_hooks
            or self.config_files or self.compose_file
        )


@dataclass(frozen=True)
class ModuleRequires:
    """Preconditions for a module to be usable, verified rather than assumed.

    A module whose requirements are unmet must be named as such: half-installed
    and silent is the state that costs the most to diagnose.
    """

    binaries: tuple[str, ...] = ()
    devices: tuple[str, ...] = ()
    groups: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()
    gpu_mb: int = 0
    # What to run to provision what `paths` asks for. The engine never runs it:
    # a module that needs 17 GB of virtualenvs and model weights is not
    # something to trigger from a timer behind someone's back. It is printed
    # when a path is missing, so "not provisioned" stops being invisible.
    setup: str = ""

    def __bool__(self) -> bool:
        return bool(
            self.binaries or self.devices or self.groups or self.paths or self.gpu_mb or self.setup
        )


def _str_tuple(value: Any, path: Path, mid: str, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ConfigError(f"{path}: module '{mid}' field '{field_name}' must be a list")
    return tuple(str(v) for v in value)


def _parse_provides(spec: Any, path: Path, mid: str) -> ModuleProvides:
    if not spec:
        return ModuleProvides()
    if not isinstance(spec, dict):
        raise ConfigError(f"{path}: module '{mid}' field 'provides' must be a map")
    unknown = sorted(set(spec) - set(_PROVIDES_KEYS))
    if unknown:
        raise ConfigError(f"{path}: module '{mid}' declares unknown provides {unknown}")
    hooks = _str_tuple(spec.get("runtime_hooks"), path, mid, "provides.runtime_hooks")
    bad_hooks = [h for h in hooks if h not in RUNTIME_HOOKS]
    if bad_hooks:
        raise ConfigError(f"{path}: module '{mid}' asks for unknown runtime hooks {bad_hooks}")
    shims_raw = spec.get("shims") or {}
    if not isinstance(shims_raw, dict):
        raise ConfigError(f"{path}: module '{mid}' field 'provides.shims' must be a map of name -> target")
    files_raw = spec.get("config_files") or {}
    if not isinstance(files_raw, dict):
        raise ConfigError(f"{path}: module '{mid}' field 'provides.config_files' must be a map of source -> destination")
    return ModuleProvides(
        shims=tuple((str(k), str(v)) for k, v in shims_raw.items()),
        systemd_units=_str_tuple(spec.get("systemd_units"), path, mid, "provides.systemd_units"),
        runtime_hooks=hooks,
        config_files=tuple((str(k), str(v)) for k, v in files_raw.items()),
        compose_file=str(spec.get("compose_file") or ""),
    )


def _parse_requires(spec: Any, path: Path, mid: str) -> ModuleRequires:
    if not spec:
        return ModuleRequires()
    if not isinstance(spec, dict):
        raise ConfigError(f"{path}: module '{mid}' field 'requires' must be a map")
    unknown = sorted(set(spec) - set(_REQUIRES_KEYS))
    if unknown:
        raise ConfigError(f"{path}: module '{mid}' declares unknown requirements {unknown}")
    gpu = spec.get("gpu_mb", 0) or 0
    if not isinstance(gpu, int) or gpu < 0:
        raise ConfigError(f"{path}: module '{mid}' field 'requires.gpu_mb' must be a non-negative integer")
    return ModuleRequires(
        binaries=_str_tuple(spec.get("binaries"), path, mid, "requires.binaries"),
        devices=_str_tuple(spec.get("devices"), path, mid, "requires.devices"),
        groups=_str_tuple(spec.get("groups"), path, mid, "requires.groups"),
        paths=_str_tuple(spec.get("paths"), path, mid, "requires.paths"),
        gpu_mb=gpu,
        setup=str(spec.get("setup") or ""),
    )


@dataclass(frozen=True)
class ModuleDef:
    id: str
    label: str
    kind: str
    states: tuple[str, ...]
    env_gates: tuple[str, ...] = ()
    mcp_servers: tuple[str, ...] = ()
    stack: str | None = None
    depends_on: tuple[str, ...] = ()
    source: str | None = None
    scope: str = "shared"
    # A command that proves the module actually works, beyond its declared
    # requirements being present. Run by the doctor, never by the guard: a
    # timer must not execute module-supplied code unattended.
    health: str = ""
    provides: ModuleProvides = field(default_factory=ModuleProvides)
    requires: ModuleRequires = field(default_factory=ModuleRequires)

    def supports(self, state: str) -> bool:
        return state in self.states

    @property
    def source_path(self) -> Path | None:
        """Where the module's own files live, with ~ expanded."""
        return Path(os.path.expanduser(self.source)) if self.source else None


@dataclass(frozen=True)
class ModuleState:
    """One module as it stands on this machine."""

    module: ModuleDef
    state: str  # absent | local | remote
    source: str  # where the verdict comes from: state-file | env-gates | default
    note: str = ""


def _catalog_path(engine_root: Path) -> Path:
    return Path(engine_root) / "agent-universal-layer" / "modules" / "modules.yaml"


def _state_file_path(vault_data: Path) -> Path:
    return Path(vault_data) / "03-INFRA" / "agent-universal-layer" / "modules.state.yaml"


def _module_from_spec(
    mid: str,
    spec: Any,
    path: Path,
    default_source: Path | None = None,
    default_scope: str = "shared",
) -> ModuleDef:
    """One catalog entry, validated. Shared by the engine catalog and by the
    self-describing manifest a module repository carries."""
    if not isinstance(spec, dict):
        raise ConfigError(f"{path}: module '{mid}' is invalid")
    kind = str(spec.get("kind", "")).strip()
    if kind not in MODULE_KINDS:
        raise ConfigError(f"{path}: module '{mid}' has unknown kind '{kind}'")
    states = tuple(str(s) for s in spec.get("states", []))
    unknown_states = [s for s in states if s not in MODULE_STATES]
    if unknown_states:
        raise ConfigError(f"{path}: module '{mid}' declares unknown states {unknown_states}")
    depends = tuple(str(d) for d in spec.get("depends_on", []))
    if mid in depends:
        raise ConfigError(f"{path}: module '{mid}' depends on itself")
    health = str(spec.get("health") or "")
    scope = str(spec.get("scope", default_scope))
    if scope not in MODULE_SCOPES:
        raise ConfigError(f"{path}: module '{mid}' has unknown scope '{scope}'; expected one of {list(MODULE_SCOPES)}")
    source = spec.get("source")
    return ModuleDef(
        id=mid,
        label=str(spec.get("label", mid)),
        kind=kind,
        states=states,
        env_gates=tuple(str(e) for e in spec.get("env_gates", [])),
        mcp_servers=tuple(str(s) for s in spec.get("mcp_servers", [])),
        stack=spec.get("stack") or None,
        depends_on=depends,
        # A repository that declares itself is its own source: repeating the
        # path inside the file it ships would just be a way to get it wrong.
        source=str(source) if source else (str(default_source) if default_source else None),
        scope=scope,
        health=health,
        provides=_parse_provides(spec.get("provides"), path, mid),
        requires=_parse_requires(spec.get("requires"), path, mid),
    )


def load_external_module(repo: Path | str) -> ModuleDef:
    """The module a repository declares about itself.

    The manifest holds exactly one module: a repository is a module, not a
    catalog. Its source defaults to the repository itself.
    """
    root = Path(os.path.expanduser(str(repo)))
    path = root / MODULE_MANIFEST
    if not path.is_file():
        raise ConfigError(f"{root}: no {MODULE_MANIFEST}; this directory does not declare a module")
    raw = _load_yaml(path, "Module manifest")
    version = raw.get("schema_version", 1)
    if version not in SCHEMA_VERSIONS:
        raise ConfigError(f"{path}: unsupported schema_version {version!r}; this engine knows {list(SCHEMA_VERSIONS)}")
    modules_raw = raw.get("modules", {})
    if not isinstance(modules_raw, dict) or len(modules_raw) != 1:
        raise ConfigError(f"{path}: must declare exactly one module under 'modules'")
    mid, spec = next(iter(modules_raw.items()))
    # A repository that ships on one machine is host-scoped unless it says
    # otherwise: forgetting to declare must not switch a module on elsewhere.
    return _module_from_spec(str(mid), spec, path, default_source=root, default_scope="host")


def load_catalog(engine_root: Path, external: Sequence[str | Path] = ()) -> dict[str, ModuleDef]:
    """Load and validate the canonical catalog. A broken catalog is a
    contract error: modules are named vocabulary, and a typo there would
    silently derail every per-machine state file."""
    path = _catalog_path(engine_root)
    raw = _load_yaml(path, "Modules catalog")
    version = raw.get("schema_version", 1)
    if version not in SCHEMA_VERSIONS:
        # Silently ignoring a newer catalog would drop whole fields on the
        # floor, which is the failure this contract exists to prevent.
        raise ConfigError(f"{path}: unsupported schema_version {version!r}; this engine knows {list(SCHEMA_VERSIONS)}")
    modules_raw = raw.get("modules", {})
    if not isinstance(modules_raw, dict):
        raise ConfigError(f"{path}: 'modules' must be a map")

    modules: dict[str, ModuleDef] = {}
    for mid, spec in modules_raw.items():
        modules[mid] = _module_from_spec(str(mid), spec, path)

    for repo in external:
        module = load_external_module(repo)
        if module.id in modules:
            raise ConfigError(f"{repo}: module '{module.id}' collides with one the engine already ships")
        if module.stack and not module.provides.compose_file:
            from nexgen_core.stack.services import by_name

            if by_name(module.stack) is None:
                raise ConfigError(
                    f"{repo}: module '{module.id}' declares stack '{module.stack}', which the engine "
                    f"does not ship. Ship it with 'provides.compose_file' or drop the field: naming a "
                    f"stack nobody knows would just be silence."
                )
        modules[module.id] = module

    for mid, module in modules.items():
        for dep in module.depends_on:
            if dep not in modules:
                raise ConfigError(f"{path}: module '{mid}' depends on unknown module '{dep}'")
    return modules


def current_host() -> str:
    """This machine's name, as used to scope declarations."""
    return socket.gethostname()


def _load_state_raw(vault_data: Path) -> dict:
    path = _state_file_path(vault_data)
    if not path.is_file():
        return {}
    raw = _load_yaml(path, "Modules state")
    version = raw.get("schema_version", 1)
    if version not in STATE_SCHEMA_VERSIONS:
        raise ConfigError(f"{path}: unsupported schema_version {version!r}; this engine knows {list(STATE_SCHEMA_VERSIONS)}")
    return raw


def load_state_file(vault_data: Path, host: str | None = None) -> dict[str, str]:
    """The declaration in force on this machine: module id -> absent|local|remote.

    The file lives in the vault, which is shared between machines, so a flat
    map declares the same thing everywhere. That was fine while every module
    was a network service; a module bound to one desktop needs to say so.
    Hence the ``hosts:`` section, whose entries override the shared map for
    that machine only. A file without it behaves exactly as before.
    """
    raw = _load_state_raw(vault_data)
    if not raw:
        return {}
    path = _state_file_path(vault_data)
    declared = raw.get("modules", {}) or {}
    if not isinstance(declared, dict):
        raise ConfigError(f"{path}: 'modules' must be a map")
    resolved = {str(k): str(v) for k, v in declared.items()}

    hosts = raw.get("hosts", {}) or {}
    if not isinstance(hosts, dict):
        raise ConfigError(f"{path}: 'hosts' must be a map of host name -> declarations")
    mine = hosts.get(host or current_host(), {}) or {}
    if not isinstance(mine, dict):
        raise ConfigError(f"{path}: declarations for host '{host or current_host()}' must be a map")
    for key, value in (mine.get("modules", {}) or {}).items():
        resolved[str(key)] = str(value)
    return resolved


def external_paths(vault_data: Path, host: str | None = None) -> list[str]:
    """Repositories this machine picks up as modules.

    Per host by nature: a module's checkout exists on the machine that has it,
    and listing it on a machine that does not would only produce a standing
    complaint about a directory that was never meant to be there.
    """
    raw = _load_state_raw(vault_data)
    if not raw:
        return []
    hosts = raw.get("hosts", {}) or {}
    mine = hosts.get(host or current_host(), {}) or {}
    if not isinstance(mine, dict):
        return []
    paths = mine.get("external", []) or []
    if not isinstance(paths, list):
        raise ConfigError(f"{_state_file_path(vault_data)}: 'external' must be a list of paths")
    return [str(p) for p in paths]


def _env_gates_satisfied(module: ModuleDef, env: dict[str, str]) -> bool:
    if not module.env_gates:
        return True
    return all(env.get(gate) for gate in module.env_gates)


def derive_state(
    modules: dict[str, ModuleDef],
    state_file: dict[str, str],
    env: dict[str, str] | None = None,
) -> list[ModuleState]:
    """What each module actually is, right now, on this machine.

    Priority: the state file declares location; the env gates prove the
    module is active at all. A declared-but-not-gated module is reported as
    inactive with a note, never as silently running. A module with no state
    declaration falls back to the env gates (active => local is the honest
    default for a tunneled setup, where the endpoint is 127.0.0.1 either
    way).
    """
    env = env if env is not None else dict(os.environ)
    results: list[ModuleState] = []
    for mid, module in modules.items():
        declared = state_file.get(mid)
        gated = _env_gates_satisfied(module, env)
        if declared is not None:
            if declared == "absent":
                results.append(ModuleState(module, "absent", "state-file"))
                continue
            if declared not in MODULE_STATES:
                results.append(ModuleState(
                    module, "absent", "state-file",
                    note=t("declared state '{declared}' is invalid; treated as absent", declared=declared),
                ))
                continue
            if not module.supports(declared):
                results.append(ModuleState(
                    module, "absent", "state-file",
                    note=t("module does not support state '{declared}'", declared=declared),
                ))
                continue
            if module.env_gates and not gated:
                results.append(ModuleState(
                    module, "absent", "env-gates",
                    note=t("declared {declared} but env gates {gates} are not set", declared=declared, gates=", ".join(module.env_gates)),
                ))
                continue
            results.append(ModuleState(module, declared, "state-file"))
            continue
        # No declaration: a module with env gates speaks for itself; a
        # module without any gate stays absent until declared, because
        # nothing on the machine proves it is on.
        if not module.env_gates:
            results.append(ModuleState(module, "absent", "default"))
            continue
        if not gated:
            results.append(ModuleState(module, "absent", "env-gates"))
            continue
        results.append(ModuleState(
            module, "local", "env-gates",
            note=t("no state declared; active via env gates, location assumed local (a tunnel looks local either way)"),
        ))
    return results


def write_state_file(
    vault_data: Path,
    *,
    module: str | None = None,
    state: str | None = None,
    host: str | None = None,
    add_external: str | None = None,
    remove_external: str | None = None,
) -> Path:
    """Change one thing in the state file, preserving everything else.

    Rewriting the file from a flat map was safe while that was all it held;
    with per-host sections in it, a blind rewrite would silently drop the
    declarations of every other machine.
    """
    path = _state_file_path(vault_data)
    raw = _load_state_raw(vault_data)
    raw.setdefault("schema_version", 2)
    raw["schema_version"] = 2
    raw.setdefault("modules", {})
    hosts = raw.setdefault("hosts", {})

    if module is not None and state is not None:
        if host:
            section = hosts.setdefault(host, {})
            section.setdefault("modules", {})[module] = state
        else:
            raw["modules"][module] = state

    target_host = host or current_host()
    if add_external or remove_external:
        section = hosts.setdefault(target_host, {})
        listed = list(section.get("external", []) or [])
        if add_external and add_external not in listed:
            listed.append(add_external)
        if remove_external and remove_external in listed:
            listed.remove(remove_external)
        if listed:
            section["external"] = listed
        else:
            section.pop("external", None)

    # Drop host sections that ended up empty, so the file stays readable.
    for name in [h for h, body in hosts.items() if not body]:
        hosts.pop(name)
    if not hosts:
        raw.pop("hosts", None)

    header = (
        "# Module state, written by 'nexgen modules set' / 'modules add'.\n"
        "# Declares where each module runs: absent (off), local (this machine),\n"
        "# remote (a machine you own, reached over a tunnel).\n"
        "#\n"
        "# This file is shared between machines through the vault. Entries under\n"
        "# 'modules:' apply everywhere; entries under 'hosts.<name>:' apply only\n"
        "# to that machine, which is what a module bound to one desktop needs.\n"
        "# 'external:' lists repositories that declare themselves modules.\n"
    )
    body = yaml.safe_dump(raw, sort_keys=True, allow_unicode=True, default_flow_style=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + body, encoding="utf-8")
    return path


def modules_state(vault_data: Path | None = None, engine_root: Path | None = None) -> list[ModuleState]:
    """One-call inventory: catalog from the engine, state from the data root."""
    from nexgen_core.paths import resolve_engine_root
    root = resolve_vault_data(None, vault_data)
    root_engine = engine_root if engine_root is not None else resolve_engine_root()
    modules = load_catalog(root_engine, external=external_paths(root))
    state_file = load_state_file(root)
    return derive_state(modules, state_file)
