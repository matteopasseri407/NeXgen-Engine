"""Size ratchet: an oversized file or function may exist, but may never grow.

Compared against the base revision, so no baseline file exists to regenerate.
"""
from __future__ import annotations

import ast
import re

from .config import Config
from .gitutil import Change
from .violations import CODE_EXT, Violation, line_count, selected


TEST_PATH = re.compile(r"(^|/)(tests?|__tests__)/|(^|/)test_[^/]*$|_test\.[^/]*$|\.(test|spec)\.[^/]*$")


def python_function_lengths(source: str | None) -> dict[str, int]:
    """Qualified name -> line count for every function, nested ones included."""
    try:
        tree = ast.parse(source or "")
    except SyntaxError:
        return {}
    lengths: dict[str, int] = {}

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{prefix}{child.name}"
                if not isinstance(child, ast.ClassDef):
                    lengths[name] = child.end_lineno - child.lineno + 1
                visit(child, f"{name}.")
            else:
                visit(child, prefix)

    visit(tree, "")
    return lengths


def _file_violation(change: Change, cfg: Config) -> Violation | None:
    new, old = line_count(change.new), line_count(change.old)
    if new > cfg.max_file_lines and (change.old is None or new > old):
        grew = f"grew from {old} lines" if change.old is not None else "is new"
        return Violation(change.path, f"{new} lines (limit {cfg.max_file_lines}), {grew}. "
                         "Do not add to this file: put the new code in a separate module.")
    return None


def _function_violations(change: Change, cfg: Config) -> list[Violation]:
    before = python_function_lengths(change.old)
    found = []
    for name, length in python_function_lengths(change.new).items():
        if length > cfg.max_func_lines and length > before.get(name, 0):
            found.append(Violation(change.path, f"function {name} is {length} lines "
                                   f"(limit {cfg.max_func_lines}). Split it; do not extend it."))
    return found


def check_sizes(changes: list[Change], cfg: Config) -> list[Violation]:
    found: list[Violation] = []
    for change in changes:
        if not selected(change, cfg, CODE_EXT):
            continue
        if not TEST_PATH.search(change.path) and (violation := _file_violation(change, cfg)):
            found.append(violation)
        if change.path.endswith(".py"):
            found.extend(_function_violations(change, cfg))
    return found
