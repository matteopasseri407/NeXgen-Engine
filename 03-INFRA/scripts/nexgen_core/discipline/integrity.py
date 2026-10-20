"""Test integrity: tests may be added freely, but not removed, weakened or skipped on the quiet.

Counts are totals over the whole change, so moving, splitting or merging tests is fine. A skip
marker is judged where it sits: one that arrives with a NEW test function gates new work (a
Windows-only reproduction), while one added to a test the base already had, or outside every
test function, is still a weakening."""
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


def _is_marker(node: ast.AST) -> bool:
    """A skip/xfail marker in real code: `pytest.mark.skipif`, or `pytest.skip(...)` inside one."""
    return isinstance(node, ast.Attribute) and ast.unparse(node) in PY_SKIPS


def marker_sites(source: str | None, path: str) -> tuple[int, dict[str, int]]:
    """Where this version's skip/xfail markers sit.

    Returns (markers outside any test function, markers per test function). In Python only real
    code counts, not the word inside a string; a non-Python test file has no functions to
    attribute them to, so all of them count as outside.
    """
    if not path.endswith(".py"):
        return len(SKIP.findall(source or "")), {}
    try:
        tree = ast.parse(source or "")
    except SyntaxError:  # nexgen: allow reason="a file that does not parse has no markers to count"
        return 0, {}
    per_test: dict[str, int] = {}
    owned: set[int] = set()
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)) or not func.name.startswith("test"):
            continue
        inside = [n for n in ast.walk(func) if _is_marker(n)]
        # Every test function is listed, zero included: the caller has to tell "this test
        # is new" from "this existing test had no marker", and an absent key says neither.
        per_test[func.name] = len(inside)
        owned.update(id(n) for n in inside)
    outside = sum(1 for n in ast.walk(tree) if _is_marker(n) and id(n) not in owned)
    return outside, per_test


def skip_growth(old: str | None, new: str | None, path: str) -> int:
    """How many skip/xfail markers this change ADDS to tests that already existed.

    A marker that arrives together with a brand-new test function is a gate on new work, the
    way a Windows-only reproduction is written, and is not a weakening. A marker added to a
    test the base already had is still refused, and so is one outside every test function
    (`pytestmark`, or any non-Python test file).
    """
    outside_before, per_test_before = marker_sites(old, path)
    outside_after, per_test_after = marker_sites(new, path)
    growth = outside_after - outside_before
    for name, count in per_test_after.items():
        if name in per_test_before:
            growth += max(0, count - per_test_before[name])
    return growth


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


def _path_is_test(path: str, cfg: Config) -> bool:
    return (TEST_PATH.search(path) is not None and path.lower().endswith(CODE_EXT)
            and not any(fnmatch(path, g) for g in cfg.test_removal_ok))


def _is_test(change: Change, cfg: Config) -> bool:
    return _path_is_test(change.path, cfg) or bool(change.old_path and _path_is_test(change.old_path, cfg))


def check_test_integrity(changes: list[Change], cfg: Config) -> list[Violation]:
    tests = [c for c in changes if _is_test(c, cfg)]
    if not tests:
        return []
    problems = []
    if sum(skip_growth(c.old, c.new, c.path) for c in tests) > 0:
        problems.append("a skip/xfail marker was added")
    py = [c for c in tests if c.path.endswith(".py") or bool(c.old_path and c.old_path.endswith(".py"))]
    (t0, a0), (t1, a1) = (tuple(map(sum, zip(*(test_stats(getattr(c, side)) for c in py), strict=False))) or (0, 0)
                          for side in ("old", "new"))
    if t1 < t0:
        problems.append(f"tests went from {t0} to {t1}")
    if a1 < a0:
        problems.append(f"assertions went from {a0} to {a1}")
    others = [c for c in tests if c not in py]
    if sum(other_language_tests(c.new) for c in others) < sum(other_language_tests(c.old) for c in others):
        problems.append("fewer test cases in non-Python test files")
    if not problems:
        return []
    return [Violation(", ".join(c.path for c in tests[:3]), f"tests weakened: {'; '.join(problems)}. {HOW_TO_ALLOW}", "test")]
