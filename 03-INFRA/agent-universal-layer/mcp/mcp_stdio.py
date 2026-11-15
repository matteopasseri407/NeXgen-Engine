#!/usr/bin/env python3
"""mcp_stdio.py — the stdio a front end speaks on: UTF-8, whatever code page the machine started with.

An MCP front end speaks JSON to another program, never to a console, but Python still decodes and
encodes that pipe in the machine's legacy code page on Windows (cp1252 on an Italian install). A
result carrying one character outside that page — an arrow, a CJK name, a checkmark — then kills the
call with `UnicodeEncodeError`, the client reads only "calling tools/call: internal error", and an
argument carrying one arrives at the server as mojibake. That is the same failure
`nexgen_core.console` fixes for the engine's own commands; the front ends are started by a CLI as
standalone scripts, so the rule is repeated here rather than imported from a package they cannot
count on being importable in the process that starts them.
"""
from __future__ import annotations

import sys


def use_utf8() -> None:
    """Point the standard streams at UTF-8, keeping a stream that cannot be moved.

    A stream with no `reconfigure` (a test's capture buffer) and a closed one
    both stay as they are: a front end still has to answer, and the encoding it
    inherited is the one it would have had before this call.
    """
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):  # nexgen: allow reason="uno stream gia' chiuso o non riconfigurabile non deve impedire al front end di rispondere: resta la codifica che aveva"
            continue
