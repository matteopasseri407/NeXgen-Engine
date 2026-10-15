"""Pinning dello split plan prima del refactor.

Fissa il comportamento attuale (non quello desiderato): build_sync_plan su
env finto ritorna il piano atteso, _git_probe stabile senza rete, firme
pubbliche invariate. Solo finti in tmp_path, mai macchina vera, mai rete.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.plan import SyncPlan, build_sync_plan  # noqa: E402

pytestmark = pytest.mark.filterwarnings("ignore")


def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket
    import urllib.request

    def _blocked(*args, **kwargs):
        raise AssertionError("rete toccata")

    monkeypatch.setattr(urllib.request, "urlopen", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


def _make_fake_layout(tmp_path: Path) -> tuple[Path, Path, Path]:
    home = tmp_path / "home"
    vault = tmp_path / "vault"
    engine = tmp_path / "engine"
    for d in (home, vault, engine):
        d.mkdir(parents=True, exist_ok=True)
    assert str(home).startswith(str(tmp_path))
    assert str(vault).startswith(str(tmp_path))
    return home, vault, engine


def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _no_network(monkeypatch)
    monkeypatch.setenv("KNOWLEDGE_VAULT_REMOTE", "local")
    monkeypatch.delenv("KNOWLEDGE_VAULT_MIRRORS", raising=False)
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir(exist_ok=True)
    monkeypatch.setenv("PATH", str(empty_bin))


def test_build_sync_plan_su_env_finto_ritorna_piano_atteso(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    home, vault, engine = _make_fake_layout(tmp_path)

    plan = build_sync_plan(home=home, vault_data=vault, engine_root=engine)

    assert isinstance(plan, SyncPlan)
    assert plan.vault == str(vault)
    assert plan.branch is None
    assert plan.commit is None
    assert any("not a Git repository" in note for note in plan.git_notes)
    assert plan.drift == bool(plan.planned_actions)
    assert plan.drift, "su env finto vuoto qualche azione e attesa"
    payload = plan.to_dict()
    assert set(payload) == {
        "engine_version", "vault", "branch", "commit", "drift",
        "git_notes", "planned_actions", "in_sync",
        "needs_attention", "not_checked",
    }
    assert payload["vault"] == str(vault)
    assert payload["drift"] == plan.drift
    assert plan.not_checked, "i limiti del piano vanno dichiarati, non taciuti"
    assert any("shims" in line or "Launcher" in line for line in plan.not_checked)
    # Stabile: due build sullo stesso env finto coincidono.
    again = build_sync_plan(home=home, vault_data=vault, engine_root=engine)
    assert again.to_dict() == payload


def test_git_probe_stabile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    _, vault, _ = _make_fake_layout(tmp_path)

    import nexgen_core.plan as plan_mod

    first = plan_mod._git_probe(vault)
    second = plan_mod._git_probe(vault)
    assert first == second
    notes, actions = first
    assert any("not a Git repository" in note for note in notes)
    assert actions == []


def test_firme_pubbliche_invariate() -> None:
    import nexgen_core.plan as plan_mod

    assert list(inspect.signature(plan_mod.build_sync_plan).parameters) == [
        "home", "vault_data", "engine_root",
    ]
    assert list(inspect.signature(plan_mod.SyncPlan.to_dict).parameters) == ["self"]
    assert isinstance(inspect.getattr_static(plan_mod.SyncPlan, "drift"), property)
