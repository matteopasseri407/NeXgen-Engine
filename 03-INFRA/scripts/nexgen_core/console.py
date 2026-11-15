"""The engine's own output streams: UTF-8, whatever code page the console started with.

One job: make `sys.stdout` and `sys.stderr` encode as UTF-8 before a command
prints, so a character the engine means reaches every reader as the bytes of that
character.

A Windows console whose code page is not UTF-8 encodes what the engine prints in
that code page, and a command whose output is a pipe is encoded the same way.
That is how `·` became the single byte `0xB7` and reached the Claude Code hook,
which decodes the line as UTF-8, as `�` in every session's first context; the
same bytes are mangled in any redirected run. Two entry points print for another
program to decode — `nexgen` and the local lane — so the rule lives here once
rather than in each of them.
"""
from __future__ import annotations

import sys


def use_utf8() -> None:
    """Point both standard streams at UTF-8, keeping a stream that cannot be moved.

    A stream that has no `reconfigure` (a test's capture buffer) and one that is
    already closed both stay as they are: the command still has to run, and the
    encoding it inherited is the one it would have had before this call.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):  # nexgen: allow reason="uno stdout gia' chiuso o non riconfigurabile non deve impedire al verbo di girare: resta la codifica che aveva"
            continue
