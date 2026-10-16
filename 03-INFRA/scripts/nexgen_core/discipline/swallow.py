"""Swallowed exceptions: a Python `except` that silently does nothing may exist, but not multiply."""
from __future__ import annotations

import ast

from .config import Config
from .gitutil import Change
from .violations import TEST_PATH, Violation, selected

MARKER = "nexgen: allow"


def _silent(handler: ast.ExceptHandler) -> bool:
    body = handler.body
    if all(isinstance(s, ast.Pass) or (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant)) for s in body):
        return True
    only = body[0] if len(body) == 1 else None
    return isinstance(only, ast.Return) and (only.value is None or (
        isinstance(only.value, ast.Constant) and only.value.value in (None, False)))


def silent_handlers(source: str | None) -> list[int]:
    """Line numbers of handlers that swallow the error and carry no `nexgen: allow` note."""
    try:
        tree = ast.parse(source or "")
    except SyntaxError:
        return []
    lines = (source or "").splitlines()
    return [h.lineno for h in ast.walk(tree) if isinstance(h, ast.ExceptHandler) and _silent(h)
            and MARKER not in lines[h.lineno - 1]]


def check_swallowed(changes: list[Change], cfg: Config) -> list[Violation]:
    found = []
    for change in changes:
        if not selected(change, cfg, (".py",)) or TEST_PATH.search(change.path):
            continue
        now, before = silent_handlers(change.new), silent_handlers(change.old)
        if len(now) > len(before):
            found.append(Violation(change.path, f"{len(now) - len(before)} new except block(s) that swallow the error "
                                   f"(lines {', '.join(map(str, now))}). Raise, record the failure, or explain on the "
                                   "except line with `# nexgen: allow reason=\"...\"`.", "swallow"))
    return found
