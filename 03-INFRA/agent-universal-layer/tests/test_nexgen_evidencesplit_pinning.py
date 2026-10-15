"""Pinning dello split verify/grounding di evidence prima del refactor.

Fissa il comportamento attuale (non quello desiderato) con evidenze finte:
risposta fondata passa, risposta inventata e' segnalata (mai silenzio),
esito parziale (una fonte letta piu' una non letta) dichiara solo la fonte
mancante. Solo ricevute finte in memoria, nessuna rete, nessun LLM reale.
"""
from __future__ import annotations

import inspect
from pathlib import Path

from nexgen_local.evidence import verify_answer


def _read_ok(path: str) -> dict:
    return {"tool": "read_vault", "args": {"path": path}, "ok": True}


def test_grounded_answer_passes() -> None:
    receipts = [_read_ok("01-NOTE/airone-blu.md")]
    assert verify_answer("Airone Blu e' un progetto [01-NOTE/airone-blu.md].", receipts) == []
    web = [{"tool": "web_search", "args": {"query": "libro"}, "ok": True}]
    assert (
        verify_answer(
            "Il libro e' scritto in inglese.", web, "Il libro e' scritto in inglese, 200 pagine."
        )
        == []
    )


def test_invented_answer_is_flagged_not_silent() -> None:
    assert verify_answer("Ho letto la nota e l'ho riassunta.", [])
    failed = [{"tool": "search_vault", "args": {"query": "falco"}, "ok": False}]
    problems = verify_answer("Il budget di Falco Rosso e' 900 euro.", failed, "")
    assert problems, "fatti senza contenuto recuperato: divieto di silenzio"
    assert any("senza contenuto recuperato" in problem for problem in problems)


def test_partial_outcome_declares_only_the_missing_source() -> None:
    receipts = [_read_ok("01-NOTE/buona.md")]
    problems = verify_answer(
        "Riassunto da [01-NOTE/buona.md] con dettagli da [99-ALTRE/inventata.md].",
        receipts,
    )
    assert problems == ["cita una fonte non letta: 99-ALTRE/inventata.md"]


def test_public_signatures_unchanged() -> None:
    signature = inspect.signature(verify_answer)
    assert list(signature.parameters) == ["answer", "receipts", "collected"]
    assert signature.parameters["collected"].default is None


def test_verify_and_grounding_helpers_in_sequence() -> None:
    import nexgen_local.evidence as evidence_mod

    src = Path(evidence_mod.__file__).read_text(encoding="utf-8")
    assert "def _check_grounding" in src
    assert "def _verify_answer" in src
    body = src[src.index("def verify_answer") :]
    assert "_check_grounding(" in body
    assert "_verify_answer(" in body
    assert body.index("_check_grounding(") < body.index("_verify_answer(")
