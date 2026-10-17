"""entry_span finds an entry's block in the two spellings a manifest really has.

`entry_span` is the one place that locates an entry's text so a caller can edit
one field without re-serializing the file. It used to know a single spelling, the
hand-written `  name:`, and the callers treat "not found" as "nothing to do": the
entry silently kept its old pin, or the adopt reported that it could not touch it.
"""
from __future__ import annotations

import json

from nexgen_core.manifest_text import entry_span


MANIFEST = (
    "servers:\n"
    "  hand-written:\n"
    "    exposure: manual\n"
    f"  {json.dumps('quoted-name')}:\n"
    "    exposure: manual\n"
    "    deps:\n"
    "      kind: npx\n"
    "  after:\n"
    "    exposure: eager\n"
)


def test_a_hand_written_key_is_one_block():
    span = entry_span(MANIFEST, "hand-written")
    assert span is not None
    block = MANIFEST[span[0]:span[1]]
    assert block.startswith("  hand-written:\n")
    assert "exposure: manual" in block
    assert "after" not in block


def test_a_quoted_key_is_the_same_entry():
    """json.dumps writes `"quoted-name":`, and the entry is still that entry."""
    span = entry_span(MANIFEST, "quoted-name")
    assert span is not None, "la chiave fra virgolette non è stata trovata"
    block = MANIFEST[span[0]:span[1]]
    assert block.startswith(f"  {json.dumps('quoted-name')}:\n")
    assert "kind: npx" in block
    assert "after" not in block
