"""Le fonti dichiarate all'answer vengono solo dalle letture riuscite.

`evidence.py` filtra le ricevute riuscite prima di decidere cosa e' stato letto;
`sources_from_receipts` no, quindi una fonte fallita entrava nel prompt e, se il
modello la citava, il controllo delle citazioni la bocciava come inventata.
"""
from __future__ import annotations

from nexgen_local.engine import sources_from_receipts
from nexgen_local.tools import ToolCall


def test_a_failed_read_is_not_a_source():
    calls = [
        ToolCall(name="read_vault", args={"path": "01-NOTE/letta.md"}, ok=True, chars=10),
        ToolCall(name="read_vault", args={"path": "01-NOTE/fallita.md"}, ok=False, chars=0),
        ToolCall(name="read_mail", args={"id": "m1"}, ok=False, chars=0),
        ToolCall(name="web_search", args={"query": "airone blu"}, ok=True, chars=5),
    ]

    assert sources_from_receipts(calls) == ["01-NOTE/letta.md", "ricerca web: airone blu"]


def test_the_other_readers_keep_their_shape():
    calls = [
        ToolCall(name="read_drive", args={"id": "d9"}, ok=True, chars=3),
        ToolCall(name="read_calendar", args={"id": "c1"}, ok=True, chars=3),
        ToolCall(name="read_pdf", args={"path": "REPO/x.pdf"}, ok=True, chars=3),
        ToolCall(name="search_vault", args={"query": "q"}, ok=True, chars=3),
    ]

    assert sources_from_receipts(calls) == ["drive:d9", "calendar:c1", "REPO/x.pdf"]
