"""The credentials this machine's secrets deposit materialized: `~/.config/nexgen/secrets.env` (`export K='v'`).

Nothing loads that file into a graphical session or a service, so a token that is safely in the deposit still never
reached a CLI started from a launcher: "Supabase / Vercel are not found". Whoever reads it for a server's credential
(the gateway when it starts the server, the doctor when it asks whether the token is there) goes through this one
reader, so they agree on what is in it and on when the file is not to be trusted.

The file is the user's own and only the user's: it is ignored when someone else owns it or anyone else can write it,
because a file a stranger could edit must not decide what a server is given. A quote left open anywhere makes the
whole file untrustworthy, so it yields nothing rather than a partial answer.
"""
from __future__ import annotations

import os
import re
import shlex
from pathlib import Path

from nexgen_core.paths import resolve_home

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CACHE: tuple[tuple, dict[str, str]] = ((), {})


def deposit_path() -> Path:
    return Path(os.environ.get("NEXGEN_SECRETS_ENV") or str(resolve_home() / ".config" / "nexgen" / "secrets.env"))


def read_deposit() -> dict[str, str]:
    """Every `export NAME=value` in the deposit's materialized file, or {} if it is absent or not to be trusted."""
    global _CACHE
    path = deposit_path()
    try:
        info = path.stat()
    except OSError:
        return {}
    if os.name != "nt" and (info.st_uid != os.getuid() or info.st_mode & 0o022):
        return {}
    signature = ((str(path), info.st_mtime_ns, info.st_size),)
    if signature == _CACHE[0]:
        return _CACHE[1]
    values: dict[str, str] = {}
    try:
        tokens = shlex.split(path.read_text(encoding="utf-8"), comments=True)
    except (OSError, ValueError):
        tokens = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "export" and index + 1 < len(tokens):
            index += 1
            token = tokens[index]
        key, sep, value = token.partition("=")
        if sep and _NAME.fullmatch(key):
            values[key] = value
        index += 1
    _CACHE = (signature, values)
    return values
