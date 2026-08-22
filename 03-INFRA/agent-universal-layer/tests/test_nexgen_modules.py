"""Test per il catalogo moduli: contratto, derivazione stato, scrittura state file.

L'installer resta AI-agent-driven, ma su basi deterministiche: il catalogo
dichiara quali stati ogni modulo supporta, `modules set` rifiuta il resto, e
la derivazione dello stato è onesta (env gates + file di stato, mai
supposizioni).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.modules import (
    ModuleState,
    derive_state,
    load_catalog,
    load_state_file,
)

CATALOG = {
    "memory": {
        "label": "Memory",
        "kind": "core",
        "states": ["local", "remote"],
        "env_gates": ["VAULT_LIBRARY_URL"],
        "mcp_servers": ["vault-library"],
    },
    "firecrawl": {
        "label": "Firecrawl",
        "kind": "connector",
        "states": ["absent", "local", "remote"],
        "env_gates": ["FIRECRAWL_TUNNEL_PORT"],
    },
    "browser": {
        "label": "Browser",
        "kind": "connector",
        "states": ["absent", "local"],
    },
    "rag": {
        "label": "RAG",
        "kind": "service",
        "states": ["absent", "local", "remote"],
        "depends_on": ["memory"],
    },
}


def _catalog_file(tmp_path: Path, body: str) -> Path:
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir(parents=True, exist_ok=True)
    path = modules_dir / "modules.yaml"
    path.write_text(body, encoding="utf-8")
    return modules_dir


def test_catalog_loads_and_validates() -> None:
    from nexgen_core.config import ConfigError
    engine = Path(__file__).resolve().parents[3] / "03-INFRA"
    catalog = load_catalog(engine)
    assert "memory" in catalog and "n8n" in catalog and "sync" in catalog
    assert catalog["browser"].supports("local") and not catalog["browser"].supports("remote")


def test_catalog_rejects_unknown_dependency(tmp_path: Path) -> None:
    from nexgen_core.config import ConfigError
    d = _catalog_file(tmp_path, """
modules:
  a:
    label: A
    kind: feature
    states: [absent, local]
    depends_on: [ghost]
""")
    with pytest.raises(ConfigError):
        load_catalog(tmp_path)


def test_derivation_declared_state_wins(tmp_path: Path) -> None:
    catalog = {mid: __import__("nexgen_core.modules", fromlist=["ModuleDef"]).ModuleDef(
        id=mid, label=v["label"], kind=v["kind"], states=tuple(v["states"]),
        env_gates=tuple(v.get("env_gates", [])),
        depends_on=tuple(v.get("depends_on", [])),
    ) for mid, v in CATALOG.items()}
    states = derive_state(catalog, {"firecrawl": "remote"}, {"FIRECRAWL_TUNNEL_PORT": "33002"})
    by_id = {s.module.id: s for s in states}
    assert by_id["firecrawl"].state == "remote" and by_id["firecrawl"].source == "state-file"


def test_derivation_declared_but_gate_missing_is_absent(tmp_path: Path) -> None:
    from nexgen_core.modules import ModuleDef
    catalog = {mid: ModuleDef(
        id=mid, label=v["label"], kind=v["kind"], states=tuple(v["states"]),
        env_gates=tuple(v.get("env_gates", [])),
        depends_on=tuple(v.get("depends_on", [])),
    ) for mid, v in CATALOG.items()}
    states = derive_state(catalog, {"firecrawl": "remote"}, {})
    by_id = {s.module.id: s for s in states}
    assert by_id["firecrawl"].state == "absent"
    assert "env gates" in by_id["firecrawl"].note


def test_derivation_no_gates_absent_until_declared(tmp_path: Path) -> None:
    from nexgen_core.modules import ModuleDef
    catalog = {mid: ModuleDef(
        id=mid, label=v["label"], kind=v["kind"], states=tuple(v["states"]),
        env_gates=tuple(v.get("env_gates", [])),
        depends_on=tuple(v.get("depends_on", [])),
    ) for mid, v in CATALOG.items()}
    states = derive_state(catalog, {}, {"SOME": "env"})
    by_id = {s.module.id: s for s in states}
    assert by_id["browser"].state == "absent"
    assert by_id["rag"].state == "absent"


def test_state_file_write_roundtrip(tmp_path: Path) -> None:
    from nexgen_core.modules import _state_file_path
    vault = tmp_path / "vault"
    path = _state_file_path(vault)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("schema_version: 1\nmodules:\n  memory: remote\n", encoding="utf-8")
    assert load_state_file(vault) == {"memory": "remote"}
