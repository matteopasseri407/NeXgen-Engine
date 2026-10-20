"""What pytest really collects on a unit's interval, and the environment it runs in.

One job: prove that the unit's changes did not make pytest collect fewer tests
than the base did, and hand the engine the one environment a unit cannot fill.
An AST count sees a deleted test function but not `__test__ = False`, a folder
pytest skips (`tests/.hidden/`, `tests/build/`), a `collect_ignore` in a conftest
or a `conftest.py` that exits before anything is collected: only a real
`--collect-only` sees those. Both trees are exported with `git archive` and
unpacked with no git metadata in them, so the code the collection imports has no
remote to push to, and the unit's own copy is never run in, so the caches a suite
leaves behind cannot make it dirty.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

from nexgen_core.discipline.violations import TEST_PATH
from nexgen_core.processes import no_window_kwargs

#: The declared budget for one collection: the base and the head have one each.
COLLECT_TIMEOUT_S = 300.0
#: Environment keys that would let the copy decide what the engine's Python imports and runs.
UNTRUSTED_ENV = ("PYTHONPATH", "PYTHONSTARTUP", "PYTHONHOME", "PYTHONUSERBASE", "PYTEST_ADDOPTS", "PYTEST_PLUGINS")
#: The folder names pytest treats as test folders, wherever they sit in the tree.
TEST_DIRS = ("tests", "test", "__tests__")
#: Files that decide how pytest discovers and runs, named in the package for the reviewer.
PYTEST_CONFIG_NAMES = ("pytest.ini", "tox.ini", "setup.cfg")
#: One collected test, as `-q` lists it: `path/to/test_x.py::test_name[case]`.
_NODE_ID = re.compile(r"^[^\s:]+::\S+")
#: pytest's own summary line, singular for one test.
_COLLECTED = re.compile(r"(\d+) tests? collected")
_NO_TESTS = re.compile(r"no tests collected")


@dataclass(frozen=True)
class Count:
    """One tree's answer: how many tests pytest collected, or why it could not say."""

    ok: bool
    tests: int
    reason: str = ""


def cleaned_environment() -> dict[str, str]:
    """The environment the unit may not fill: the copy decides nothing about the interpreter.

    `-P` keeps the working directory off `sys.path` but does not touch `PYTHONPATH`,
    so a `PYTHONPATH=.` inherited from the parent puts the copy back at the top of
    `sys.path` and its own `pytest.py` runs again. `PYTEST_ADDOPTS` and
    `PYTEST_PLUGINS` are the same idea one layer out, and a package in the user
    site directory is not the engine's either.
    """
    environment = dict(os.environ)
    for key in UNTRUSTED_ENV:
        environment.pop(key, None)
    environment["PYTHONNOUSERSITE"] = "1"
    return environment


def check(
    folder: Path, base: str, head: str, files: list, pytest_surface: list[str],
    *, timeout: float = COLLECT_TIMEOUT_S,
) -> tuple[bool, str]:
    """Whether the head still collects what the base collected, with both numbers in the reason."""
    whole, dirs = paths_to_collect(files, pytest_surface)
    if not whole and not dirs:
        return True, "nessun test né configurazione di pytest toccato: niente da confrontare"
    before, after = counts(folder, base, head, dirs=() if whole else dirs, timeout=timeout)
    return _verdict(before, after)


def surface(files: list) -> list[str]:
    """The pytest files a unit touched: a conftest, a config, or pyproject's pytest section.

    These files decide what the suite collects and how. They are not a failure:
    a unit may legitimately add a fixture. The package names them so a reviewer
    looks at them before saying PASS, and the collected count swaps to the whole
    suite when one of them changed, because one file changes every folder.
    """
    found: set[str] = set()
    for change in files:
        for path in _both_names(change):
            name = Path(path).name
            if name == "conftest.py" or name in PYTEST_CONFIG_NAMES:
                found.add(path)
            elif name == "pyproject.toml" and "[tool.pytest" in ((change.new or "") + (change.old or "")):
                found.add(path)
    return sorted(found)


def paths_to_collect(files: list, pytest_surface: list[str]) -> tuple[bool, list[str]]:
    """Whether the whole configured suite is counted, and which test folders the unit touched.

    A touched pytest configuration or `conftest.py` changes how every folder is
    collected, so the whole suite is counted. Otherwise only the test folders that
    hold a touched file: the folder is taken from the name of the test directory,
    not from the file's own parent, so a test moved into `tests/.hidden/` is
    counted against `tests/` and shows up as a test that stopped being collected.
    """
    if pytest_surface:
        return True, []
    dirs = sorted({_test_root(path) for change in files for path in _both_names(change) if _is_test_file(path)})
    return False, dirs


def counts(
    repo: Path, base: str, head: str, *, dirs: tuple[str, ...] | list[str] = (),
    timeout: float = COLLECT_TIMEOUT_S,
) -> tuple[Count, Count]:
    """The base's count and the head's, each from its own exported tree; no path means the whole suite."""
    with tempfile.TemporaryDirectory(prefix="nexgen-collect-", ignore_cleanup_errors=True) as scratch:
        root = Path(scratch)
        targets = [str(name) for name in dirs]
        return (
            _count_at(repo, base, root / "base", targets, timeout),
            _count_at(repo, head, root / "head", targets, timeout),
        )


def _count_at(repo: Path, sha: str, copy: Path, dirs: list[str], timeout: float) -> Count:
    """One revision exported to `copy` and collected there; a folder absent from that tree holds no test."""
    problem = _export(repo, sha, copy, timeout)
    if problem:
        return Count(False, 0, problem)
    targets = [name for name in dirs if (copy / name).exists()]
    if dirs and not targets:
        return Count(True, 0)
    return _collect(copy, targets, timeout)


def _export(repo: Path, sha: str, copy: Path, timeout: float) -> str:
    """One committed tree unpacked with no `.git` in it, or why it was not.

    A detached worktree would keep the repository's configuration and its remotes,
    and the code a collection imports is the unit's own: a test that ran
    `git push` from such a copy would find the owner's remote with the push block
    missing, because that block lives in the unit copy's per-worktree config.
    """
    copy.mkdir(parents=True, exist_ok=True)
    tarball = copy.parent / "tree.tar"
    argv = ["git", "-C", str(repo), "archive", "--format=tar", sha]
    try:
        with tarball.open("wb") as handle:
            done = subprocess.run(
                argv, stdout=handle, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                errors="replace", stdin=subprocess.DEVNULL, timeout=timeout, check=False, **no_window_kwargs(),
            )
    except subprocess.TimeoutExpired:
        return f"albero temporaneo non esportato: git archive oltre {timeout:.0f}s"
    except OSError as exc:
        return f"albero temporaneo non esportato ({type(exc).__name__}: {exc})"
    if done.returncode:
        return f"git archive esce con {done.returncode}: {_last_line(done.stderr)}"
    try:
        # TarFile is tarfile.open's own reader, named directly because the engine's
        # encoding scan reads every call called `open` as text IO, and this one reads
        # an archive.
        with tarfile.TarFile(name=str(tarball), mode="r") as archive:
            archive.extractall(copy, filter="data")
    except (tarfile.TarError, OSError) as exc:
        return f"albero temporaneo non scompattato ({type(exc).__name__}: {exc})"
    return ""


def _collect(folder: Path, targets: list[str], timeout: float) -> Count:
    """pytest's own answer, run by the engine's interpreter with `-P` and a cleaned environment."""
    argv = [sys.executable, "-P", "-m", "pytest", "--collect-only", "-q", *targets]
    try:
        done = subprocess.run(
            argv, cwd=folder, capture_output=True, text=True, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL, timeout=timeout, env=cleaned_environment(), check=False, **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return Count(False, 0, f"tempo massimo superato ({timeout:.0f}s)")
    except OSError as exc:
        return Count(False, 0, f"raccolta non avviata ({type(exc).__name__}: {exc})")
    output = (done.stdout or "") + (done.stderr or "")
    if done.returncode not in (0, 5):  # 5 is pytest's "no test was collected", which is a number, not a failure
        return Count(False, 0, f"pytest --collect-only esce con {done.returncode}: {_last_line(output)}")
    found = _count(output)
    if found is None:
        detail = _last_line(output) or "nessun output"
        return Count(False, 0, "pytest non ha detto quanti test ha raccolto: " + detail)
    return Count(True, found)


def _count(output: str) -> int | None:
    """The tests pytest listed, cross-checked with its own summary so a printed line cannot outvote it."""
    listed = sum(1 for line in output.splitlines() if _NODE_ID.match(line.strip()))
    if _NO_TESTS.search(output):
        return 0 if not listed else None
    summary = _COLLECTED.search(output)
    if summary is None or int(summary.group(1)) != listed:
        return None
    return listed


def _verdict(before: Count, after: Count) -> tuple[bool, str]:
    """FAIL when either collection could not answer or the head lost a test; the numbers are always named."""
    if not before.ok:
        return False, f"la raccolta della base non ha risposto ({before.reason}): nessun numero con cui confrontare la head"
    if not after.ok:
        return False, f"la raccolta della head non ha risposto ({after.reason}); la base ne raccoglieva {before.tests}"
    if after.tests < before.tests:
        return False, f"la head raccoglie {after.tests} test, la base {before.tests}: la raccolta è diminuita"
    return True, f"la head raccoglie {after.tests} test, la base {before.tests}"


def _both_names(change) -> list[str]:
    """Both names of a rename: a test moved out of collection is a change at the old name too."""
    old = getattr(change, "old_path", None)
    return [change.path] + ([old] if old else [])


def _is_test_file(path: str) -> bool:
    """A Python file pytest would look for: a test-named file or a file inside a test folder."""
    return path.lower().endswith(".py") and TEST_PATH.search(path) is not None


def _test_root(path: str) -> str:
    """The test folder a path sits in, or the file itself when it sits outside one."""
    parts = path.split("/")
    for index, part in enumerate(parts[:-1]):
        if part.casefold() in TEST_DIRS:
            return "/".join(parts[: index + 1])
    return path


def _last_line(text: str) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return (lines[-1] if lines else "")[:200]
