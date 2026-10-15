"""Pinning dello split spec/derive/state di modules prima del refactor.

Fissa il comportamento attuale (non quello desiderato) con cataloghi finti:
spec finta caricata con moduli attesi, derive su stato finto stabile,
write_state_file scrive solo se cambia. Solo tmp_path, solo cataloghi finti,
nessuna rete, nessun catalogo vero.
"""
from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.modules import (
    ModuleDef,
    derive_state,
    load_catalog,
    load_state_file,
    write_state_file,
)

FAKE_CATALOG_YAML = """\
schema_version: 2
modules:
  fake-memory:
    label: Fake Memory
    kind: core
    states: [local, remote]
    env_gates: [FAKE_VAULT_URL]
  fake-firecrawl:
    label: Fake Firecrawl
    kind: connector
    states: [absent, local, remote]
    env_gates: [FAKE_FIRECRAWL_PORT]
  fake-browser:
    label: Fake Browser
    kind: connector
    states: [absent, local]
"""


def _fake_engine_root(tmp_path: Path) -> Path:
    engine_root = tmp_path / "engine"
    mods = engine_root / "agent-universal-layer" / "modules"
    mods.mkdir(parents=True, exist_ok=True)
    (mods / "modules.yaml").write_text(FAKE_CATALOG_YAML, encoding="utf-8")
    return engine_root


def _fake_catalog() -> dict[str, ModuleDef]:
    return {
        "fake-memory": ModuleDef(
            id="fake-memory", label="Fake Memory", kind="core",
            states=("local", "remote"), env_gates=("FAKE_VAULT_URL",),
        ),
        "fake-firecrawl": ModuleDef(
            id="fake-firecrawl", label="Fake Firecrawl", kind="connector",
            states=("absent", "local", "remote"), env_gates=("FAKE_FIRECRAWL_PORT",),
        ),
        "fake-browser": ModuleDef(
            id="fake-browser", label="Fake Browser", kind="connector",
            states=("absent", "local"),
        ),
    }


def test_fake_spec_loads_expected_modules(tmp_path: Path) -> None:
    engine_root = _fake_engine_root(tmp_path)
    catalog = load_catalog(engine_root)
    assert set(catalog) == {"fake-memory", "fake-firecrawl", "fake-browser"}
    assert catalog["fake-memory"].kind == "core"
    assert catalog["fake-memory"].supports("local")
    assert catalog["fake-memory"].supports("remote")
    assert not catalog["fake-browser"].supports("remote")
    assert catalog["fake-firecrawl"].env_gates == ("FAKE_FIRECRAWL_PORT",)


def test_derive_on_stable_fake_state(tmp_path: Path) -> None:
    catalog = _fake_catalog()
    env = {"FAKE_VAULT_URL": "https://fake.invalid", "FAKE_FIRECRAWL_PORT": "33002"}
    state_file = {"fake-memory": "local", "fake-firecrawl": "remote"}
    states = derive_state(catalog, state_file, env)
    by_id = {s.module.id: s for s in states}
    assert by_id["fake-memory"].state == "local"
    assert by_id["fake-memory"].source == "state-file"
    assert by_id["fake-firecrawl"].state == "remote"
    assert by_id["fake-firecrawl"].source == "state-file"
    # Modulo senza gate e senza dichiarazione resta assente finche' non dichiarato.
    assert by_id["fake-browser"].state == "absent"
    assert by_id["fake-browser"].source == "default"


def test_write_state_file_writes_only_if_changed(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    first = write_state_file(vault, module="fake-memory", state="local")
    assert first.is_file()
    assert load_state_file(vault) == {"fake-memory": "local"}
    content_before = first.read_text(encoding="utf-8")
    # Fissa mtime nel passato: una seconda scrittura identica non deve toccare il file.
    old_mtime = first.stat().st_mtime - 100.0
    os.utime(first, (old_mtime, old_mtime))
    second = write_state_file(vault, module="fake-memory", state="local")
    assert second == first
    assert second.read_text(encoding="utf-8") == content_before
    assert second.stat().st_mtime == old_mtime


def test_write_state_file_preserves_other_entries(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    write_state_file(vault, module="fake-memory", state="local")
    write_state_file(vault, module="fake-firecrawl", state="remote")
    assert load_state_file(vault) == {"fake-memory": "local", "fake-firecrawl": "remote"}
    write_state_file(vault, module="fake-memory", state="remote")
    assert load_state_file(vault) == {"fake-memory": "remote", "fake-firecrawl": "remote"}


def test_public_signatures_unchanged() -> None:
    import nexgen_core.modules as modules_mod

    assert list(inspect.signature(modules_mod.load_catalog).parameters) == [
        "engine_root", "external",
    ]
    assert list(inspect.signature(modules_mod.derive_state).parameters) == [
        "modules", "state_file", "env",
    ]
    assert list(inspect.signature(modules_mod.write_state_file).parameters) == [
        "vault_data", "module", "state", "host", "add_external", "remove_external",
    ]
    assert list(inspect.signature(modules_mod.modules_state).parameters) == [
        "vault_data", "engine_root",
    ]


def test_spec_derive_state_helpers_in_sequence() -> None:
    import nexgen_core.modules as modules_mod

    src = Path(modules_mod.__file__).read_text(encoding="utf-8")
    assert "def _load_spec" in src
    assert "def _derive_state" in src
    assert "def _write_state_file" in src
    assert src.index("def _load_spec") < src.index("def _derive_state")
    assert src.index("def _derive_state") < src.index("def _write_state_file")
    load_body = src[src.index("def load_catalog") : src.index("def _load_spec")]
    assert "_load_spec(" in load_body
    derive_body = src[src.index("def derive_state") : src.index("def _derive_state")]
    assert "_derive_state(" in derive_body
    write_body = src[src.index("def write_state_file") : src.index("def _write_state_file")]
    assert "_write_state_file(" in write_body
