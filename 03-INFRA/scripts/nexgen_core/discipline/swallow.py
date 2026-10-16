"""Swallowed exceptions: a Python `except` that silently does nothing may exist, but not multiply."""
from __future__ import annotations

import ast

from .config import Config
from .gitutil import Change
from .violations import TEST_PATH, Violation, selected

MARKER = "nexgen: allow"


def _silent(handler: ast.ExceptHandler) -> bool:
    body = handler.body
    if all(isinstance(s, (ast.Pass, ast.Continue)) or (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
           for s in body):
        return True
    only = body[0] if len(body) == 1 else None
    if not isinstance(only, ast.Return):
        return False
    value = only.value
    empty_literal = isinstance(value, (ast.List, ast.Dict, ast.Tuple, ast.Set)) and not getattr(value, "elts", getattr(value, "keys", [1]))
    return value is None or empty_literal or (isinstance(value, ast.Constant) and value.value in (None, False, "", 0))


def silent_handlers(source: str | None) -> list[int]:
    """Line numbers of handlers that swallow the error and carry no `nexgen: allow` note on the except header."""
    try:
        tree = ast.parse(source or "")
    except SyntaxError:  # nexgen: allow reason="a file that does not parse has no handlers to count"
        return []
    lines = (source or "").splitlines()
    return [h.lineno for h in ast.walk(tree) if isinstance(h, ast.ExceptHandler) and _silent(h)
            and MARKER not in "\n".join(lines[h.lineno - 1:h.body[0].lineno - 1] or lines[h.lineno - 1:h.lineno])]


def check_swallowed(changes: list[Change], cfg: Config) -> list[Violation]:
    found = []
    for change in changes:
        if not selected(change, cfg, (".py",)) or TEST_PATH.search(change.path):
            continue
        now, before = silent_handlers(change.new), silent_handlers(change.old)
        if len(now) > len(before):
            found.append(Violation(change.path, f"{len(now) - len(before)} new except block(s) that swallow the error "
                                   f"(file now has {len(now)}, had {len(before)}; lines {', '.join(map(str, now))}). Raise, record the failure, or explain on the "
                                   "except line with `# nexgen: allow reason=\"...\"`.", "swallow"))
    return found
