"""The size ceiling as an alarm: what is over it joins the split queue.

Nothing here refuses a push. `check` and `gate` print what is over the ceiling as
a warning, `report` and `audit` print the same list as the splitting still owed.
A file that declares its one job at the top (`nexgen: one-job reason="..."`)
is not queued until the hard cap; a function has no such escape.
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


def _growth(change: Change) -> str:
    """Which way the file moved, when the change carries its parent revision."""
    if change.old is None:
        return ""
    before, after = line_count(change.old), line_count(change.new)
    if after > before:
        return f", grew from {before} lines"
    return f", shrank from {before} lines" if after < before else ""


def _file_violation(change: Change, cfg: Config) -> Violation | None:
    lines = line_count(change.new)
    if lines <= cfg.max_file_lines:
        return None
    if declares_one_job(change.new) and lines <= ONE_JOB_CAP:
        return None
    return Violation(change.path, f"{lines} lines (limit {cfg.max_file_lines}){_growth(change)}. "
                     "One file does one job: split it, or say what its one job is at the top with "
                     f"`nexgen: one-job reason=\"...\"` (up to {ONE_JOB_CAP} lines).", "file")


def _function_violations(change: Change, cfg: Config) -> list[Violation]:
    if not change.path.endswith(".py"):
        return []
    return [Violation(change.path, f"function {name} is {length} lines (limit {cfg.max_func_lines}): "
                       "split it when you next touch it.", "function")
            for name, length in python_function_lengths(change.new).items() if length > cfg.max_func_lines]


def split_queue(changes: list[Change], cfg: Config) -> list[Violation]:
    """Files and functions over the ceiling in this set of files: the work list of splits."""
    found: list[Violation] = []
    for change in changes:
        if not selected(change, cfg, CODE_EXT):
            continue
        if not TEST_PATH.search(change.path) and (violation := _file_violation(change, cfg)):
            found.append(violation)
        found.extend(_function_violations(change, cfg))
    return found
