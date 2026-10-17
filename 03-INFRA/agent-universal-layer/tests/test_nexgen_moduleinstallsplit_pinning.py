"""Pinning del comportamento attuale di module_install prima dello split.

Solo home finte in tmp_path, mai la home vera, mai installazioni reali
fuori dalla sandbox, mai rete.
"""
from __future__ import annotations

import inspect
from pathlib import Path

from nexgen_core import module_install
from nexgen_core.module_install import (
    check_requirements,
    install_declared_modules,
    install_module,
    shim_path,
    uninstall_module,
)
from nexgen_core.modules import ModuleDef, ModuleProvides, ModuleRequires


def _fake_module(tmp_path: Path, name: str = "pin-demo") -> ModuleDef:
    src = tmp_path / "src" / "bin"
    src.mkdir(parents=True, exist_ok=True)
    target = src / "entry"
    target.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    return ModuleDef(
        id=name,
        label="Pin",
        kind="feature",
        states=("absent", "local"),
        provides=ModuleProvides(shims=((name, str(target)),)),
        requires=ModuleRequires(),
    )


def test_check_requirements_on_fake_home_returns_outcome(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    empty = ModuleDef(id="pin-empty", label="P", kind="feature", states=("absent", "local"))
    assert check_requirements(empty, home) == []

    missing = ModuleDef(
        id="pin-missing",
        label="P",
        kind="feature",
        states=("absent", "local"),
        requires=ModuleRequires(binaries=("questo-binario-non-esiste-davvero-xyz",)),
    )
    unmet = check_requirements(missing, home)
    assert unmet
    assert any(u.kind == "binary" and u.blocks_install for u in unmet)


def test_uninstall_on_fake_module_cleans(tmp_path: Path) -> None:
    module = _fake_module(tmp_path)
    home = tmp_path / "home"
    installed = install_module(module, home=home)
    assert installed
    shim = shim_path(home / ".local" / "bin", "pin-demo")
    assert shim.is_file()

    removed = uninstall_module(module, home=home)
    assert removed
    assert not shim.exists()

    assert uninstall_module(module, home=home) == []


def test_install_on_fake_home_is_idempotent(tmp_path: Path) -> None:
    module = _fake_module(tmp_path, name="pin-idem")
    home = tmp_path / "home"
    first = install_module(module, home=home)
    second = install_module(module, home=home)
    assert first and not second


def test_public_signatures_unchanged() -> None:
    assert list(inspect.signature(install_module).parameters) == [
        "module", "home", "engine_root", "dry_run", "log",
    ]
    assert list(inspect.signature(uninstall_module).parameters) == [
        "module", "home", "dry_run", "log",
    ]
    assert list(inspect.signature(check_requirements).parameters) == ["module", "home"]
    assert list(inspect.signature(install_declared_modules).parameters) == [
        "states", "home", "engine_root", "dry_run", "log",
    ]
    # I tre entry point restano funzioni pubbliche del modulo.
    for name in ("install_module", "uninstall_module", "check_requirements"):
        assert callable(getattr(module_install, name))
