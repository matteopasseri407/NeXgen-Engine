"""The module catalog: what the engine is made of, and its per-machine state.

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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nexgen_core.config import ConfigError, _load_yaml
from nexgen_core.i18n import t
from nexgen_core.paths import resolve_vault_data

MODULE_KINDS = ("core", "connector", "service", "feature")
MODULE_STATES = ("absent", "local", "remote")


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

    def supports(self, state: str) -> bool:
        return state in self.states


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


def load_catalog(engine_root: Path) -> dict[str, ModuleDef]:
    """Load and validate the canonical catalog. A broken catalog is a
    contract error: modules are named vocabulary, and a typo there would
    silently derail every per-machine state file."""
    path = _catalog_path(engine_root)
    raw = _load_yaml(path, "Modules catalog")
    modules_raw = raw.get("modules", {})
    if not isinstance(modules_raw, dict):
        raise ConfigError(f"{path}: 'modules' must be a map")

    modules: dict[str, ModuleDef] = {}
    for mid, spec in modules_raw.items():
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
        for dep in depends:
            if dep == mid:
                raise ConfigError(f"{path}: module '{mid}' depends on itself")
        modules[mid] = ModuleDef(
            id=mid,
            label=str(spec.get("label", mid)),
            kind=kind,
            states=states,
            env_gates=tuple(str(e) for e in spec.get("env_gates", [])),
            mcp_servers=tuple(str(s) for s in spec.get("mcp_servers", [])),
            stack=spec.get("stack") or None,
            depends_on=depends,
        )
    for mid, module in modules.items():
        for dep in module.depends_on:
            if dep not in modules:
                raise ConfigError(f"{path}: module '{mid}' depends on unknown module '{dep}'")
    return modules


def load_state_file(vault_data: Path) -> dict[str, str]:
    """The per-machine declaration: module id -> absent|local|remote.

    Missing file is fine: the machine simply has not declared anything, and
    derivation falls back to the env gates. A declaration for an unknown
    module or an unsupported state is a data error: it is named, never
    silently ignored.
    """
    path = _state_file_path(vault_data)
    if not path.is_file():
        return {}
    raw = _load_yaml(path, "Modules state")
    declared = raw.get("modules", {})
    if not isinstance(declared, dict):
        raise ConfigError(f"{path}: 'modules' must be a map")
    return {str(k): str(v) for k, v in declared.items()}


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


def _env_gates_satisfied(module: ModuleDef, env: dict[str, str]) -> bool:
    if not module.env_gates:
        return True
    return all(env.get(gate) for gate in module.env_gates)


def modules_state(vault_data: Path | None = None, engine_root: Path | None = None) -> list[ModuleState]:
    """One-call inventory: catalog from the engine, state from the data root."""
    from nexgen_core.paths import resolve_engine_root
    root = resolve_vault_data(None, vault_data)
    root_engine = engine_root if engine_root is not None else resolve_engine_root()
    modules = load_catalog(root_engine)
    state_file = load_state_file(root)
    return derive_state(modules, state_file)
