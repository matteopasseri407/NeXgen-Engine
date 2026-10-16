"""Per-commit proof of tested work.

Critical code never ships without a test in the same commit, and a commit titled `fix...:` must show that
its test fails on the code it fixes (run against the parent), so a test that passes by construction is caught.
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import tempfile
from fnmatch import fnmatch
from pathlib import Path

from ..files import atomic_write_text
from .config import Config
from .gitutil import git
from .violations import CODE_EXT, SHELL_EXT, TEST_PATH, Violation

FIX = re.compile(r"^fix(\([^)]*\))?!?:", re.IGNORECASE)
NO_TEST = re.compile(r'nexgen:\s*no-test\s+reason="[^"]{10,}"')
PUBLISHED_REFS = ("origin/main", "main")
TIMEOUT = 600


def _commits(root: Path, base: str, head: str) -> list[str]:
    """Commits authored on this branch: merged-in published history was judged when it was pushed."""
    published = [f"^{ref}" for ref in PUBLISHED_REFS if git(root, "rev-parse", "--verify", "-q", ref, check=False).strip()]
    return git(root, "rev-list", "--reverse", "--no-merges", f"{base}..{head}", *published, check=False).split()


def _touched(root: Path, commit: str) -> list[str]:
    return git(root, "diff-tree", "-r", "--no-renames", "--name-only", "--diff-filter=AM", f"{commit}^", commit).splitlines()


def _critical(path: str, cfg: Config) -> bool:
    return (path.lower().endswith(CODE_EXT + SHELL_EXT) and not TEST_PATH.search(path)
            and any(fnmatch(path, g) for g in cfg.review_paths) and not any(fnmatch(path, g) for g in cfg.review_exempt))


def _fails_before(root: Path, commit: str, tests: list[str], command: str) -> str | None:
    """None when the commit's tests fail on the parent's code; otherwise why that was not shown."""
    argv = shlex.split(command)
    if argv[0] in ("python", "python3"):
        argv[0] = sys.executable
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp) / "before"
        git(root, "worktree", "add", "--detach", str(tree), f"{commit}^")
        try:
            for test in tests:  # the new tests, against the old code
                (tree / test).parent.mkdir(parents=True, exist_ok=True)
                atomic_write_text(tree / test, git(root, "show", f"{commit}:{test}"))
            done = subprocess.run([*argv, *tests], cwd=tree, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=TIMEOUT,
                                  env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        except (OSError, subprocess.TimeoutExpired) as err:
            return f"could not run `{command}` on the code before the fix ({err})"
        finally:
            git(root, "worktree", "remove", "--force", str(tree), check=False)
    if done.returncode == 1:
        return None
    if done.returncode == 0:
        return "its tests also pass on the code before the fix, so they prove nothing about the bug. Write a test that fails without the fix"
    tail = (done.stdout + done.stderr).strip().splitlines()[-3:]
    return f"could not show its tests failing before the fix (exit {done.returncode}: {' | '.join(tail)})"


def check_commits(root: Path, cfg: Config, base: str, head: str) -> list[Violation]:
    found: list[Violation] = []
    escape = 'or write `nexgen: no-test reason="..."` in the commit message if no test is possible'
    for commit in _commits(root, base, head):
        if not git(root, "rev-parse", "--verify", "-q", f"{commit}^", check=False).strip():
            continue
        message = git(root, "show", "-s", "--format=%B", commit)
        if NO_TEST.search(message):
            continue
        subject = message.split("\n", 1)[0]
        touched = _touched(root, commit)
        tests = [p for p in touched if TEST_PATH.search(p) and p.lower().endswith(CODE_EXT)]
        label = f"commit {commit[:8]} ({subject[:50]})"
        if FIX.match(subject) and not tests:
            found.append(Violation(label, f"a fix needs a test that fails before it and passes after; add it, {escape}", "regress"))
        elif any(_critical(p, cfg) for p in touched) and not tests:
            found.append(Violation(label, f"critical code changed without a test in the same commit; add one, {escape}", "regress"))
        elif FIX.match(subject) and cfg.test_command and (py := [t for t in tests if t.endswith(".py")]):
            if (problem := _fails_before(root, commit, py, cfg.test_command)):
                found.append(Violation(label, problem, "regress"))
    return found
