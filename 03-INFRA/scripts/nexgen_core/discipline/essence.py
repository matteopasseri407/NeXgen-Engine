"""Code, or only its decoration: do two revisions of a file differ in behaviour?

The per-commit gate refuses a change to critical code that ships without a test.
A commit that rewrites a comment, a docstring or the spacing changes nothing a
test could prove, so this module answers that one question for a pair of file
revisions. Python is compared as the tree the parser sees, which ignores
comments, docstrings, blank lines and layout; every other text file is compared
line by line, without its comments and blank lines. What it cannot read, it
calls a change: the gate stays on the side that asks for a test.
"""
from __future__ import annotations

import ast

DOCSTRING_OWNERS = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def _drop_docstrings(tree: ast.AST) -> None:
    """A docstring is the first statement of a module, a class or a function, and only there."""
    for node in ast.walk(tree):
        if isinstance(node, DOCSTRING_OWNERS) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                del node.body[0]


def _line_essence(text: str) -> str:
    """A file the parser cannot read: its lines, without comments and blank lines."""
    return "\n".join(stripped for line in text.splitlines()
                     if (stripped := line.strip()) and not stripped.startswith("#"))


def code_essence(text: str | None) -> str | None:
    """The code a revision carries, decoration removed; None when there is no text to compare."""
    if text is None:
        return None
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return _line_essence(text)
    _drop_docstrings(tree)
    return ast.dump(tree)


def differs_in_code(old: str | None, new: str | None) -> bool:
    """True unless both revisions carry the same code: comments, docstrings and blank lines do not count."""
    before, after = code_essence(old), code_essence(new)
    return before is None or after is None or before != after
