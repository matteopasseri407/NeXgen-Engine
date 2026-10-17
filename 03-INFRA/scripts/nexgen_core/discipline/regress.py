"""Per-commit proof of tested work.

Critical code never ships without a test in the same commit, and a commit titled `fix...:` that changes code must
show its tests failing on the parent's code and passing on its own, so a test that passes by construction (or
never runs) is caught. The title is the trigger for the proof; the critical-code rule applies whatever the title
says. A change that only touches comments, docstrings or blank lines changes no behaviour, so it is not a code
change here and asks for no test.
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

from ..files import atomic_write_text
from .config import Config
from .essence import differs_in_code
from .gitutil import git, show
from .score import is_critical
from .violations import CODE_EXT, SHELL_EXT, TEST_PATH, Violation

FIX = re.compile(r"^fix(\([^)]*\))?!?:", re.IGNORECASE)
NO_TEST = re.compile(r'nexgen:\s*no-test\s+reason="[^"]{10,}"')
TIMEOUT = 600
NO_TESTS_RAN = 5  # pytest: everything collected was skipped or deselected
#: Environment keys that point git at the outer repository. A proof runs another
#: revision's tests: when this tool is started by a git hook (a push from a
#: linked worktree exports GIT_DIR), test code running `git -C <tmp> config`
#: would edit the real repository instead of its own scratch copy.
GIT_CONTEXT_KEYS = (
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_PREFIX",
)


def _child_env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key not in GIT_CONTEXT_KEYS} | {
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def _commits(root: Path, base: str, head: str) -> list[str]:
    """Commits that no remote has yet: published history was judged when it was pushed.

    `--not --remotes`, not `^origin/main`: a rewrite of history leaves old
    commits unreachable from the branch they came from while they are still on a
    remote, and judging those again asked 181 already published commits for
    their tests and blocked a push (2026-10-17).
    """
    is_commit = git(root, "rev-parse", "--verify", "-q", f"{base}^{{commit}}", check=False).strip()
    return git(root, "rev-list", "--reverse", "--no-merges", f"{base}..{head}" if is_commit else head,
               "--not", "--remotes").split()


def _touched(root: Path, commit: str) -> list[tuple[str, str | None]]:
    """(path, previous path if moved) for every file the commit added, changed or moved."""
    out = git(root, "diff-tree", "-r", "-M", "--name-status", "--diff-filter=AMR", f"{commit}^", commit)
    rows = [line.split("\t") for line in out.splitlines()]
    return [(r[-1], r[1] if r[0][0] == "R" else None) for r in rows if len(r) > 1]


def _run(root: Path, rev: str, tests: list[str], command: str, new_tests_from: str) -> subprocess.CompletedProcess:
    argv = shlex.split(command, posix=os.name != "nt")
    if argv[0] in ("python", "python3"):
        argv[0] = sys.executable
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp) / "tree"
        git(root, "worktree", "add", "--detach", str(tree), rev)
        try:
            for test in tests:  # the commit's tests, whichever code they run against
                (tree / test).parent.mkdir(parents=True, exist_ok=True)
                atomic_write_text(tree / test, git(root, "show", f"{new_tests_from}:{test}"))
            return subprocess.run([*argv, *tests], cwd=tree, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  timeout=TIMEOUT, env=_child_env(), stdin=subprocess.DEVNULL)
        finally:
            git(root, "worktree", "remove", "--force", str(tree), check=False)


def _tail(done: subprocess.CompletedProcess) -> str:
    return " | ".join((done.stdout + done.stderr).strip().splitlines()[-3:])


def _proof_problem(root: Path, commit: str, tests: list[str], command: str) -> str | None:
    """None when the tests fail before the change and pass after it; otherwise why that was not shown."""
    try:
        after = _run(root, commit, tests, command, commit)
        if after.returncode == NO_TESTS_RAN:
            return "its tests were all skipped here (a missing optional dependency?), so nothing was proved"
        if after.returncode:
            return f"its tests do not pass after the fix, or the runner is missing (exit {after.returncode}: {_tail(after)})"
        before = _run(root, f"{commit}^", tests, command, commit)
    except (OSError, subprocess.TimeoutExpired) as err:
        return f"could not run `{command}` ({err})"
    if before.returncode == 0:
        return "its tests also pass on the code before the fix, so they prove nothing about the bug. Write a test that fails without the fix"
    return None


def _differs_in_code(root: Path, commit: str, paths: list[str]) -> set[str]:
    """The subset of `paths` whose code really changed: not a comment, a docstring or a blank line.

    A path that did not exist in the parent revision (a new file, a move) counts as changed.
    """
    return {path for path in paths
            if differs_in_code(show(root, f"{commit}^:{path}"), show(root, f"{commit}:{path}"))}


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
        tests = [p for p, _ in touched if TEST_PATH.search(p) and p.lower().endswith(CODE_EXT)]
        code = [p for p, _ in touched if p.lower().endswith(CODE_EXT + SHELL_EXT) and not TEST_PATH.search(p)]
        critical = [p for p, old in touched if p in code and is_critical(cfg, p, old)]
        is_fix_title = bool(FIX.match(subject))
        # A comment, a docstring or a blank line changes nothing a test could prove.
        written = _differs_in_code(root, commit, code if is_fix_title else critical)
        code = [p for p in code if p in written]
        critical = [p for p in critical if p in written]
        label = f"commit {commit[:8]} ({subject[:50]})"
        is_fix = is_fix_title and bool(code)  # a fix to tests, docs or config has no code to prove
        if (is_fix or critical) and not tests:
            what = "a fix needs a test that fails before it and passes after" if is_fix else "critical code changed without a test in the same commit"
            found.append(Violation(label, f"{what}; add it, {escape}", "regress"))
        elif is_fix and cfg.test_command and (py := [t for t in tests if t.endswith(".py")]):
            if (problem := _proof_problem(root, commit, py, cfg.test_command)):
                found.append(Violation(label, problem, "regress"))
    return found
