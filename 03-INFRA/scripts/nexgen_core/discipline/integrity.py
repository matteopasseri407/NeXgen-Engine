"""Test integrity: tests may be added freely, but not removed, weakened or skipped on the quiet.

Counts are totals over the whole change, so moving, splitting or merging tests is fine."""
from __future__ import annotations

import ast
import re
from fnmatch import fnmatch

from .config import Config
from .gitutil import Change
from .violations import CODE_EXT, TEST_PATH, Violation, selected

PY_SKIPS = {"pytest.mark.skip", "pytest.mark.skipif", "pytest.mark.xfail", "pytest.skip", "pytest.xfail",
            "unittest.skip", "unittest.skipIf", "unittest.skipUnless"}
SKIP = re.compile(r"pytest\.mark\.(skip|skipif|xfail)|pytest\.(skip|xfail)\(|unittest\.skip|\b(describe|it|test)\.skip\(|\bx(it|describe|test)\(|@Ignore")
HOW_TO_ALLOW = "If this is intended, the owner adds the path to test_removal_ok in .nexgen-discipline.toml first."


def _cases(func: ast.AST) -> int:
    """How many test cases a function stands for: a literal parametrize list counts each entry."""
    for deco in getattr(func, "decorator_list", ()):
        if isinstance(deco, ast.Call) and getattr(deco.func, "attr", "") == "parametrize" and len(deco.args) > 1 \
                and isinstance(deco.args[1], (ast.List, ast.Tuple)):
            return max(len(deco.args[1].elts), 1)
    return 1


def count_skips(change_text: str | None, path: str) -> int:
    """Skip/xfail markers. In Python only real code counts, not the word inside a string."""
    if not path.endswith(".py"):
        return len(SKIP.findall(change_text or ""))
    try:
        tree = ast.parse(change_text or "")
    except SyntaxError:  # nexgen: allow reason="a file that does not parse has no markers to count"
        return 0
    return sum(isinstance(n, ast.Attribute) and ast.unparse(n) in PY_SKIPS for n in ast.walk(tree))


def test_stats(source: str | None) -> tuple[int, int]:
    """(test cases, assertions) in a Python test file; (0, 0) if it does not parse."""
    try:
        tree = ast.parse(source or "")
    except SyntaxError:
        return 0, 0
    tests = asserts = 0
    for func in ast.walk(tree):
        if isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)) and func.name.startswith("test"):
            weight = _cases(func)
            tests += weight
            for node in ast.walk(func):
                is_call = isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")).startswith("assert")
                asserts += weight * (isinstance(node, ast.Assert) or is_call)
    return tests, asserts


def _is_test(change: Change, cfg: Config) -> bool:
    return (TEST_PATH.search(change.path) is not None and change.path.lower().endswith(CODE_EXT)
            and not any(fnmatch(change.path, g) for g in cfg.test_removal_ok))


def check_test_integrity(changes: list[Change], cfg: Config) -> list[Violation]:
    tests = [c for c in changes if _is_test(c, cfg)]
    if not tests:
        return []
    problems = []
    skips_before = sum(count_skips(c.old, c.path) for c in tests)
    skips_after = sum(count_skips(c.new, c.path) for c in tests)
    if skips_after > skips_before:
        problems.append("a skip/xfail marker was added")
    py = [c for c in tests if c.path.endswith(".py")]
    (t0, a0), (t1, a1) = (tuple(map(sum, zip(*(test_stats(getattr(c, side)) for c in py), strict=False))) or (0, 0)
                          for side in ("old", "new"))
    if t1 < t0:
        problems.append(f"tests went from {t0} to {t1}")
    if a1 < a0:
        problems.append(f"assertions went from {a0} to {a1}")
    others = [c for c in tests if not c.path.endswith(".py")]
    if any(c.new is None for c in others) and not any(c.old is None for c in others):
        problems.append("a test file was deleted")
    if not problems:
        return []
    return [Violation(", ".join(c.path for c in tests[:3]), f"tests weakened: {'; '.join(problems)}. {HOW_TO_ALLOW}", "test")]
