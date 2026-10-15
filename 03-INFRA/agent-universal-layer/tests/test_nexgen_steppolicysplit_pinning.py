"""Pinning dello split di step_policy.py: menu chiuso, assistito e validazione.

Solo stati finti, nessuna rete, nessuna azione reale.
Fissa il comportamento attuale prima della separazione in helper:
- menu chiuso su stato finto contiene le voci attese,
- menu assistito differisce dal chiuso sullo stesso stato,
- azione con provenienza sospetta (query con termini dal contenuto) rifiutata.
"""
from __future__ import annotations

from pathlib import Path

from nexgen_local.config import LaneConfig
from nexgen_local.step_policy import (
    ASSISTED_SEARCHES,
    Candidate,
    build_assisted_menu,
    build_menu,
    validate_action,
)
from nexgen_local.step_state import LoopState


def _cfg(tmp_path: Path) -> LaneConfig:
    vault = tmp_path / "vault"
    vault.mkdir(parents=True, exist_ok=True)
    return LaneConfig(
        vault_root=vault,
        repo_roots=(),
        model="fake-model",
        audit_path=tmp_path / "audit.jsonl",
    )


def test_menu_chiuso_contiene_voci_attese(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    state_none = LoopState(task="domanda semplice", route="none")
    closed_none = [c.action for c in build_menu(cfg, state_none)]
    assert closed_none == ["answer", "escalate"]
    state_vault = LoopState(task="cerca airone", route="vault")
    closed_vault = [c.action for c in build_menu(cfg, state_vault)]
    assert closed_vault == ["search_vault", "escalate"]


def test_menu_assistito_differisce_dal_chiuso(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    state = LoopState(task="cerca airone", route="vault")
    closed = build_menu(cfg, state)
    assisted = build_assisted_menu(cfg, state)
    closed_actions = {c.action for c in closed}
    assisted_actions = {c.action for c in assisted}
    for search in ASSISTED_SEARCHES:
        assert search in assisted_actions
    assert "search_web" in assisted_actions
    assert "search_web" not in closed_actions
    assert len(assisted) > len(closed)
    assert "escalate" in assisted_actions


def test_azione_provenienza_sospetta_rifiutata(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    state = LoopState(
        task="cerca airone",
        route="vault",
        content_seen=["rapporto interno che cita Parolasegreta progetto"],
        tried_queries=[],
    )
    menu = [Candidate("search_vault"), Candidate("escalate")]
    reason = validate_action(cfg, state, menu, "search_vault", "Parolasegreta progetto")
    assert reason is not None
    assert "contenuto recuperato" in reason
