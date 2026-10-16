"""Test integrity: tests may be added freely, but not removed, weakened or skipped on the quiet."""
from __future__ import annotations

import ast
import re
from fnmatch import fnmatch

from .config import Config
from .gitutil import Change
from .violations import TEST_PATH, Violation

SKIP = re.compile(r"pytest\.mark\.(skip|skipif|xfail)|pytest\.(skip|xfail)\(|unittest\.skip|\b(xit|xdescribe|xtest)\(|\.skip\(|@Ignore")
HOW_TO_ALLOW = "If this is intended, the owner adds the path to test_removal_ok in .nexgen-discipline.toml first."


def test_stats(source: str | None) -> tuple[int, int]:
    """(test functions, assertions) in a Python test file; (0, 0) if it does not parse."""
    try:
        tree = ast.parse(source or "")
    except SyntaxError:
        return 0, 0
    tests = sum(1 for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test"))
    asserts = sum(isinstance(n, ast.Assert) for n in ast.walk(tree))
    asserts += sum(isinstance(n, ast.Call) and getattr(n.func, "attr", getattr(n.func, "id", "")).startswith("assert")
                   for n in ast.walk(tree))
    return tests, asserts


def _weakened(change: Change) -> list[str]:
    problems = []
    if len(SKIP.findall(change.new)) > len(SKIP.findall(change.old)):
        problems.append("a skip/xfail marker was added")
    if change.path.endswith(".py"):
        (t0, a0), (t1, a1) = test_stats(change.old), test_stats(change.new)
        if t1 < t0:
            problems.append(f"tests went from {t0} to {t1}")
        if a1 < a0:
            problems.append(f"assertions went from {a0} to {a1}")
    return problems


def check_test_integrity(changes: list[Change], cfg: Config) -> list[Violation]:
    found = []
    for change in changes:
        if not TEST_PATH.search(change.path) or any(fnmatch(change.path, g) for g in cfg.test_removal_ok):
            continue
        if change.new is None:
            found.append(Violation(change.path, f"test file deleted. {HOW_TO_ALLOW}", "test"))
        elif change.old is not None and (problems := _weakened(change)):
            found.append(Violation(change.path, f"tests weakened: {'; '.join(problems)}. {HOW_TO_ALLOW}", "test"))
    return found
