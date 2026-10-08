"""Surgical edits to a manifest's text: find one entry's block without re-serializing the file.

Manifests are hand-written and commented; round-tripping them through a YAML library loses the comments. Callers
that change one field of one entry edit the text inside that entry's block and leave every other byte alone.
"""
from __future__ import annotations

import re


def entry_span(text: str, name: str) -> tuple[int, int] | None:
    """(start, end) offsets of the `  name:` block, or None.

    Manifest entries are two-space-indented maps with deeper-indented bodies; the block runs until a blank line,
    a less-indented line, or the next entry. Anything outside the block (comments, other entries) is never touched.
    """
    match = re.search(rf"^  {re.escape(name)}:\s*\n", text, re.MULTILINE)
    if not match:
        return None
    end = match.end()
    for line in text[end:].splitlines(keepends=True):
        if line.strip() == "" or line.startswith("    ") or re.match(r"^  #", line):
            end += len(line)
        else:
            break
    return match.start(), end
