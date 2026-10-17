"""Size ratchet: an oversized file or function may exist, but may never grow.

Compared against the base revision, so no baseline file exists to regenerate.
"""
from __future__ import annotations

import ast
import re

from .config import Config
from .gitutil import Change
from .violations import CODE_EXT, TEST_PATH, Violation, line_count, selected


ONE_JOB = re.compile(r"nexgen:\s*one-job\b.*reason\s*=\s*[\"']?(\S[^\"']{9,})", re.I)
ONE_JOB_WINDOW = 15
ONE_JOB_CAP = 1000  # even a file with a single job stops here


def declares_one_job(text: str | None) -> bool:
    """`nexgen: one-job reason="..."` in the first lines: the author says what the one job of this big file is."""
    return any(ONE_JOB.search(line) for line in (text or "").splitlines()[:ONE_JOB_WINDOW])


def python_function_lengths(source: str | None) -> dict[str, int]:
    """Qualified name -> line count for every function, nested ones included."""
    try:
        tree = ast.parse(source or "")
    except SyntaxError:  # nexgen: allow reason="unparseable source has no measurable functions; the syntax check reports it"
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
    if declares_one_job(change.new) and (new <= ONE_JOB_CAP or (change.old is not None and new <= old)):
        return None
    if new > cfg.max_file_lines and (change.old is None or new > old):
        grew = f"grew from {old} lines" if change.old is not None else "is new"
        return Violation(change.path, f"{new} lines (limit {cfg.max_file_lines}), {grew}. "
                         "One file does one job: if this one does a single thing, say so at the top with `nexgen: one-job reason=\"what its one job is\"` (up to 1000 lines); otherwise put the new code in a separate module.", "file")
    return None


def _function_violations(change: Change, cfg: Config) -> list[Violation]:
    before = python_function_lengths(change.old)
    found = []
    for name, length in python_function_lengths(change.new).items():
        if length > cfg.max_func_lines and length > before.get(name, 0):
            found.append(Violation(change.path, f"function {name} is {length} lines "
                                   f"(limit {cfg.max_func_lines}). Split it; do not extend it.", "function"))
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
