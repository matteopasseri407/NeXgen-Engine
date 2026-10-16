"""Test integrity: tests may be added freely, but not removed, weakened or skipped on the quiet.

Counts are totals over the whole change, so moving, splitting or merging tests is fine."""
from __future__ import annotations

import ast
import re
from fnmatch import fnmatch

from .config import Config
from .gitutil import Change
from .violations import CODE_EXT, TEST_PATH, Violation

PY_SKIPS = {"pytest.mark.skip", "pytest.mark.skipif", "pytest.mark.xfail", "pytest.skip", "pytest.xfail",
            "unittest.skip", "unittest.skipIf", "unittest.skipUnless"}
SKIP = re.compile(r"pytest\.mark\.(skip|skipif|xfail)|pytest\.(skip|xfail)\(|unittest\.skip|\b(describe|it|test)\.skip\(|\bx(it|describe|test)\(|@Ignore")
OTHER_TESTS = re.compile(r"\b(?:it|test)\s*\(|\bfunc Test\w+|@Test\b")
HOW_TO_ALLOW = "If this is intended, the owner adds the path to test_removal_ok in .nexgen-discipline.toml first."


def count_skips(change_text: str | None, path: str) -> int:
    """Skip/xfail markers. In Python only real code counts, not the word inside a string."""
    if not path.endswith(".py"):
        return len(SKIP.findall(change_text or ""))
    try:
        tree = ast.parse(change_text or "")
    except SyntaxError:  # nexgen: allow reason="a file that does not parse has no markers to count"
        return 0
    return sum(isinstance(n, ast.Attribute) and ast.unparse(n) in PY_SKIPS for n in ast.walk(tree))


def _literal_sizes(tree: ast.AST) -> dict[str, int]:
    """Module-level `NAME = [..]`/`(..)` lists, so `parametrize("x", NAME)` can be weighed."""
    return {t.id: len(n.value.elts) for n in ast.walk(tree) if isinstance(n, ast.Assign)
            and isinstance(n.value, (ast.List, ast.Tuple)) for t in n.targets if isinstance(t, ast.Name)}


def _cases(func: ast.AST, sizes: dict[str, int]) -> int:
    """How many test cases a function stands for: a parametrize list (literal or named) counts each entry."""
    for deco in getattr(func, "decorator_list", ()):
        if isinstance(deco, ast.Call) and getattr(deco.func, "attr", "") == "parametrize" and len(deco.args) > 1:
            cases = deco.args[1]
            size = len(cases.elts) if isinstance(cases, (ast.List, ast.Tuple)) else sizes.get(getattr(cases, "id", ""), 1)
            return max(size, 1)
    return 1


def test_stats(source: str | None) -> tuple[int, int]:
    """(test cases, assertions) in a Python test file; assertions in helpers count too. (0, 0) if it does not parse."""
    try:
        tree = ast.parse(source or "")
    except SyntaxError:  # nexgen: allow reason="a file that does not parse has nothing to count"
        return 0, 0
    sizes = _literal_sizes(tree)
    asserting = {id(n) for n in ast.walk(tree) if isinstance(n, ast.Assert) or (isinstance(n, ast.Call) and getattr(
        n.func, "attr", getattr(n.func, "id", "")).startswith("assert"))}
    tests = asserts = 0
    for func in ast.walk(tree):
        if isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)) and func.name.startswith("test"):
            weight = _cases(func, sizes)
            tests += weight
            inside = {id(n) for n in ast.walk(func)} & asserting
            asserts += weight * len(inside)
            asserting -= inside  # helpers outside any test function keep weight 1
    return tests, asserts + len(asserting)


def other_language_tests(source: str | None) -> int:
    """Rough count of test cases in non-Python files: it(/test( in JS, func Test in Go, @Test in Java."""
    return len(OTHER_TESTS.findall(source or ""))


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
    if sum(other_language_tests(c.new) for c in others) < sum(other_language_tests(c.old) for c in others):
        problems.append("fewer test cases in non-Python test files")
    if not problems:
        return []
    return [Violation(", ".join(c.path for c in tests[:3]), f"tests weakened: {'; '.join(problems)}. {HOW_TO_ALLOW}", "test")]
