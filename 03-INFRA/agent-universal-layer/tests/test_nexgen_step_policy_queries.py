"""Quali query la policy accetta, per fonte, e cosa resta da leggere.

Due regole distinte: l'esfiltrazione (solo il web) e il riuso dei termini presi
da ciò che il motore ha già recuperato. Per il vault i termini devono nominare un
file vero; per le fonti personali il termine letto in una email è il seguito
normale del lavoro, non una deviazione.
"""
from __future__ import annotations

from pathlib import Path

from nexgen_local.config import LaneConfig
from nexgen_local.step_policy import _check_search_query, _pending_unread
from nexgen_local.step_state import LoopState


def _cfg(tmp_path: Path) -> LaneConfig:
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    return LaneConfig(
        vault_root=vault,
        repo_roots=(),
        model="fake-model",
        audit_path=tmp_path / "audit.jsonl",
        drafts_dir=tmp_path / "drafts",
    )


def test_a_personal_search_may_reuse_a_term_read_in_that_mail(tmp_path: Path) -> None:
    state = LoopState(task="Riassumi la mail di ieri", route="mail")
    state.content_seen = ["Oggetto: Falco Rosso, budget 900 euro"]

    assert _check_search_query(_cfg(tmp_path), state, "search_mail", "falco rosso") is None
    assert _check_search_query(_cfg(tmp_path), state, "search_outlook", "falco rosso") is None


def test_a_vault_search_still_refuses_a_term_that_is_not_a_file(tmp_path: Path) -> None:
    state = LoopState(task="Riassumi la nota airone", route="vault")
    state.content_seen = ["Il termine parolasegreta compare qui."]

    assert _check_search_query(_cfg(tmp_path), state, "search_vault", "parolasegreta") == (
        "query con termini presi dal contenuto recuperato"
    )


def test_a_web_search_still_refuses_a_retrieved_term(tmp_path: Path) -> None:
    state = LoopState(task="Cerca sul web", route="web")
    state.content_seen = ["da una nota: parolasegreta"]

    assert _check_search_query(_cfg(tmp_path), state, "search_web", "parolasegreta") == (
        "query con termini presi dal contenuto recuperato"
    )


def test_a_vault_search_with_hits_to_read_pending_refuses_the_answer() -> None:
    state = LoopState(task="Riassumi airone", route="vault")
    state.hits = ["01-NOTE/airone.md"]

    assert _pending_unread(state).startswith("risultati vault ancora da leggere")

    state.reads = ["01-NOTE/airone.md"]
    assert _pending_unread(state) == ""
