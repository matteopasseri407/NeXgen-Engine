"""I comandi dell'engine vanno nella home che il ruolo riceve, mai in quella vera.

Il passo dei launcher risolveva `resolve_home()` da solo, ignorando la home
passata a `run_standalone_role` / `run_node_role`. Effetto: un test in sandbox
riscriveva i comandi di questa macchina verso una cartella in `/tmp` che poi
spariva, e `agent-doctor`, `agent-now`, `agent-skill` smettevano di funzionare.
Era la quarta volta; qui la proprieta' e' fissata dove il guasto stava.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import bootstrap, roles, shims  # noqa: E402

pytestmark = pytest.mark.filterwarnings("ignore")


def _engine_root(tmp_path: Path) -> Path:
    root = tmp_path / "engine"
    (root / "03-INFRA" / "scripts" / "nexgen_core").mkdir(parents=True)
    return root


def _no_packaged_install(monkeypatch: pytest.MonkeyPatch) -> None:
    """A checkout, not a pipx install: the launchers are ours to write."""
    monkeypatch.setattr(shims, "installed_as_package", lambda: False)


def test_launcher_step_writes_into_the_home_it_was_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_packaged_install(monkeypatch)
    root = _engine_root(tmp_path)
    home = tmp_path / "home"

    roles._install_launchers_step(root, lambda _line: None, home)  # noqa: SLF001

    assert (home / ".local" / "bin" / ("nexgen.cmd" if sys.platform == "win32" else "nexgen")).is_file()


def test_node_sync_threads_the_home_it_was_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Il passo condiviso dal ruolo nodo: `home` arriva fino ai launcher."""
    _no_packaged_install(monkeypatch)
    root = _engine_root(tmp_path)
    home = tmp_path / "home"
    for name in ("write_user_profile", "write_remotes", "seed_skill_manifest"):
        monkeypatch.setattr(roles, name, MagicMock())

    roles._sync_node_role(root, lambda _line: None, lambda: (True, "ok"), home)  # noqa: SLF001

    assert (home / ".local" / "bin").is_dir(), "i comandi sono finiti fuori dalla home del ruolo"


def test_bootstrap_install_launchers_honours_an_explicit_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La docstring prometteva la home come parametro, il codice la risolveva da solo."""
    _no_packaged_install(monkeypatch)
    root = _engine_root(tmp_path)
    home = tmp_path / "home"

    bootstrap.install_launchers(root, home)

    assert (home / ".local" / "bin").is_dir()
