"""What pytest really collects on a unit's interval, and the environment it runs in.

One job: prove the head does not collect fewer tests than the base, name the node
ids that went, and accept a drop only when the base's own rules authorised the
files those tests lived in. An AST count sees a deleted test function but not
`__test__ = False`, a folder pytest skips (`tests/.hidden/`, `tests/build/`), a
`collect_ignore` in a conftest or a `conftest.py` that exits before anything is
collected: only a real `--collect-only` sees those, and only the node ids it
lists say *which* test went. The count is the floor and the node ids are the
extension: a renamed test or a changed parametrisation moves an id without
losing a test, so the ids that went are named as information until the count
falls. The owner authorises a retirement by naming the file in
`test_removal_ok` on the base, the way `nexgen_core/discipline/integrity.py`
already does; the head's own copy of that file is never read, because a unit does
not grant itself permission. Both trees are exported with `git archive` and
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
from fnmatch import fnmatch
from pathlib import Path

from nexgen_core.discipline import config
from nexgen_core.discipline.config import ConfigError
from nexgen_core.discipline.violations import TEST_PATH
from nexgen_core.processes import no_window_kwargs

from . import supervision_retirement as own_rules

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
    """One tree's answer: which tests pytest collected there, or why it could not say."""

    ok: bool
    nodes: tuple[str, ...] = ()
    reason: str = ""

    @property
    def tests(self) -> int:
        """How many tests this tree collects."""
        return len(self.nodes)


def cleaned_environment() -> dict[str, str]:
    """The environment the unit may not fill: the copy decides nothing about the interpreter.

    `-P` keeps the working directory off `sys.path` but does not touch `PYTHONPATH`,
    so a `PYTHONPATH=.` inherited from the parent puts the copy back at the top of
    `sys.path` and its own `pytest.py` runs again. `PYTEST_ADDOPTS` and
    `PYTEST_PLUGINS` are the same idea one layer out.

    The user site directory is left alone: it lives outside the unit's copy, so the
    unit cannot fill it, and the interpreters a test suite starts may rely on it.
    Forcing `PYTHONNOUSERSITE=1` made a project's own tests fail under the engine
    when a child Python took a package from there.
    """
    environment = dict(os.environ)
    for key in UNTRUSTED_ENV:
        environment.pop(key, None)
    return environment


def check(
    folder: Path, base: str, head: str, files: list, pytest_surface: list[str],
    *, timeout: float = COLLECT_TIMEOUT_S, by_commit: bool = False,
) -> tuple[bool, str]:
    """Whether the head collects at least as many tests as the base, and who authorised a drop.

    The floor is the number: a head that collects at least as many tests as the
    base passes, and the node ids that went are named as information. `by_commit`
    is the unit the supervisor let carry its own `.nexgen-discipline.toml`: a drop
    is then accepted only when the commit that caused it inherited the
    authorisation from its parent, and the base's own `test_removal_ok` is not
    consulted. Any other unit is compared with the base, because a unit does not
    authorise itself.
    """
    whole, dirs = paths_to_collect(files, pytest_surface)
    if not whole and not dirs:
        return True, "nessun test né configurazione di pytest toccato: niente da confrontare"
    if by_commit:
        return _by_commit(folder, base, head, whole, dirs, timeout)
    allowed, problem = _authorised(folder, base)
    if problem:
        return False, problem
    before, after = counts(folder, base, head, dirs=() if whole else dirs, timeout=timeout)
    return _verdict(before, after, allowed)


def _authorised(folder: Path, base: str) -> tuple[tuple[str, ...], str]:
    """The files whose tests the owner let go, read from the base's rules, and why if unreadable.

    It is the base's `.nexgen-discipline.toml`, never the head's: a unit that could
    write its own `test_removal_ok` would authorise itself, which is the one thing
    the owner's signature on the base is there to prevent.
    """
    try:
        return tuple(config.load(folder, base).test_removal_ok), ""
    except (ConfigError, OSError) as exc:
        return (), f"le regole della base non si leggono, quindi nessun ritiro è autorizzato ({exc})"


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
        return Count(False, (), problem)
    targets = [name for name in dirs if (copy / name).exists()]
    if dirs and not targets:
        return Count(True)
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
        return Count(False, (), f"tempo massimo superato ({timeout:.0f}s)")
    except OSError as exc:
        return Count(False, (), f"raccolta non avviata ({type(exc).__name__}: {exc})")
    output = (done.stdout or "") + (done.stderr or "")
    if done.returncode not in (0, 5):  # 5 is pytest's "no test was collected", which is a number, not a failure
        return Count(False, (), f"pytest --collect-only esce con {done.returncode}: {_last_line(output)}")
    found = _node_ids(output)
    if found is None:
        detail = _last_line(output) or "nessun output"
        return Count(False, (), "pytest non ha detto quali test ha raccolto: " + detail)
    return Count(True, found)


def _node_ids(output: str) -> tuple[str, ...] | None:
    """The node ids pytest listed, cross-checked with its own summary so a printed line cannot outvote it."""
    listed = tuple(line.strip() for line in output.splitlines() if _NODE_ID.match(line.strip()))
    if _NO_TESTS.search(output):
        return () if not listed else None
    summary = _COLLECTED.search(output)
    if summary is None or int(summary.group(1)) != len(listed):
        return None
    return listed


def _count(output: str) -> int | None:
    """The tests pytest listed, cross-checked with its own summary so a printed line cannot outvote it."""
    nodes = _node_ids(output)
    return None if nodes is None else len(nodes)


def _by_commit(
    folder: Path, base: str, head: str, whole: bool, dirs: list[str], timeout: float,
) -> tuple[bool, str]:
    """The same comparison, with every test the head lost traced to the commit whose parent authorised it.

    The floor is the one the base comparison uses: a head that collects at least
    as many tests as the base passes, and the ids that went are named as
    information. Only a head that collects fewer sends the walk out, and only a
    commit that lowered its own count retires anything. A test the walk cannot
    place is a FAIL, so a collection that hides a loss under somebody else's
    change is never read as a retirement.
    """
    before, after = counts(folder, base, head, dirs=() if whole else dirs, timeout=timeout)
    if not before.ok:
        return False, f"la raccolta della base non ha risposto ({before.reason}): nessun numero con cui confrontare la head"
    if not after.ok:
        return False, f"la raccolta della head non ha risposto ({after.reason}); la base ne raccoglieva {before.tests}"
    numbers = f"la head raccoglie {after.tests} test, la base {before.tests}"
    lost = sorted(set(before.nodes) - set(after.nodes))
    if after.tests >= before.tests:
        return True, _not_fewer(numbers, lost)
    retired, refused, problems = _retirements(folder, base, head, lost, timeout)
    unplaced = [node for node in lost if node not in retired]
    if problems or unplaced:
        detail = " | ".join([*problems, *([_unplaced(unplaced, refused)] if unplaced else [])])
        return False, f"{numbers}: {detail}"[:800]
    files = sorted({retired[node] for node in lost})
    return True, (
        f"{numbers}: {_how_many(len(lost), 'test ritirato', 'test ritirati')} da {', '.join(files)}, "
        "autorizzati da test_removal_ok del commit precedente"
    )


def _retirements(
    folder: Path, base: str, head: str, lost: list[str], timeout: float,
) -> tuple[dict[str, str], dict[str, str], list[str]]:
    """Which commit lost each of these tests while lowering its own count, when its inherited rules allowed it.

    Only a commit that touches a lost test's file, or a file that decides how
    pytest collects, can make a test disappear: the others cost no collection.
    The rules are always the parent's, so authorising a removal and making it in
    the same commit is refused here rather than accepted.
    """
    pending = set(lost)
    retired: dict[str, str] = {}
    refused: dict[str, str] = {}
    problems: list[str] = []
    cache: dict[tuple[str, tuple[str, ...]], Count] = {}
    for commit in own_rules.commits(folder, base, head):
        changes = own_rules.changes_of(folder, commit)
        if not changes:
            continue
        surface_files = surface(changes)
        touched = {path for change in changes for path in _both_names(change)}
        if not surface_files and not ({_file_of(node) for node in pending} & touched):
            continue
        whole, dirs = paths_to_collect(changes, surface_files)
        gone, problem = _lost_in_commit(folder, commit, () if whole else tuple(dirs), cache, timeout)
        if problem:
            problems.append(problem)
            continue
        rules = own_rules.rules_before(folder, commit)
        for node in sorted(gone & pending):
            pending.discard(node)
            if _authorised_node(node, rules.test_removal_ok):
                retired[node] = _file_of(node)
            else:
                refused[node] = commit
    return retired, refused, problems


def _lost_in_commit(
    folder: Path, commit: str, targets: tuple[str, ...],
    cache: dict[tuple[str, tuple[str, ...]], Count], timeout: float,
) -> tuple[set[str], str]:
    """The node ids this commit stopped collecting while its own count fell, against its parent's tree.

    A commit that does not lower its own count lost no test: a rename or a
    changed parametrisation moves an id and leaves the number alone, so there is
    nothing for it to authorise. A collection that cannot answer proves nothing,
    so the commit is named in the failure instead of being read as "nothing lost".
    """
    parent = own_rules.parent_of(folder, commit)
    if not parent:
        return set(), ""
    before = _count_once(folder, parent, targets, cache, timeout)
    after = _count_once(folder, commit, targets, cache, timeout)
    for moment, count in (("prima", before), ("dopo", after)):
        if not count.ok:
            return set(), f"commit {commit[:8]}: la raccolta {moment} non ha risposto ({count.reason})"
    if after.tests >= before.tests:
        return set(), ""
    return set(before.nodes) - set(after.nodes), ""


def _count_once(
    folder: Path, sha: str, targets: tuple[str, ...],
    cache: dict[tuple[str, tuple[str, ...]], Count], timeout: float,
) -> Count:
    """One revision's collection, computed once: the next commit asks for the same tree as its "before"."""
    key = (sha, targets)
    if key not in cache:
        with tempfile.TemporaryDirectory(prefix="nexgen-collect-", ignore_cleanup_errors=True) as scratch:
            cache[key] = _count_at(folder, sha, Path(scratch) / "tree", list(targets), timeout)
    return cache[key]


def _unplaced(unplaced: list[str], refused: dict[str, str]) -> str:
    """The lost tests no authorised commit accounts for, each with the commit that refused it."""
    named = [
        f"{node} (commit {refused[node][:8]})" if node in refused else node
        for node in unplaced
    ]
    return (
        f"{_how_many(len(named), 'test perso', 'test persi')} fuori dalle autorizzazioni "
        f"del commit precedente: {_listed(named)}"
    )


def _verdict(before: Count, after: Count, allowed: tuple[str, ...] = ()) -> tuple[bool, str]:
    """FAIL when either collection could not answer, or when the head collects fewer and nobody allowed it.

    The floor is the number, as it was before the node ids arrived: a head that
    collects at least as many tests as the base passes, and the ids that went are
    named only as information, because a renamed test or a changed parametrisation
    moves an id without losing a test. Only a head that collects fewer sends every
    lost node id through the base's `test_removal_ok`: `2 tests` on both sides can
    be one test gone and another arrived, but nothing was lost, and a retirement
    is per file and visible in the reason.
    """
    if not before.ok:
        return False, f"la raccolta della base non ha risposto ({before.reason}): nessun numero con cui confrontare la head"
    if not after.ok:
        return False, f"la raccolta della head non ha risposto ({after.reason}); la base ne raccoglieva {before.tests}"
    numbers = f"la head raccoglie {after.tests} test, la base {before.tests}"
    lost = sorted(set(before.nodes) - set(after.nodes))
    if after.tests >= before.tests:
        return True, _not_fewer(numbers, lost)
    refused = [node for node in lost if not _authorised_node(node, allowed)]
    if refused:
        return False, (
            f"{numbers}: {_how_many(len(refused), 'test perso', 'test persi')} fuori dai file autorizzati "
            f"da test_removal_ok: {_listed(refused)}"
        )
    files = sorted({_file_of(node) for node in lost})
    return True, (
        f"{numbers}: {_how_many(len(lost), 'test ritirato', 'test ritirati')} da {', '.join(files)}, "
        "autorizzati da test_removal_ok"
    )


def _not_fewer(numbers: str, lost: list[str]) -> str:
    """The PASS reason of the number floor, naming the node ids that went as information only."""
    if not lost:
        return numbers
    gone = _how_many(len(lost), "node id non c'è più", "node id non ci sono più")
    return (
        f"{numbers}: {gone}, ma il conteggio non cala "
        f"(rinomina o parametrizzazione cambiata): {_listed(lost)}"
    )


def _authorised_node(node: str, allowed: tuple[str, ...]) -> bool:
    """Whether the file this node id belongs to is one the owner allowed to shrink or go."""
    path = _file_of(node)
    return any(fnmatch(path, glob) for glob in allowed)


def _file_of(node: str) -> str:
    """The file a node id sits in: everything before the first `::`."""
    return node.split("::", 1)[0]


def _how_many(count: int, one: str, many: str) -> str:
    """`1 test perso`, `2 test persi`: the reason is read by a person."""
    return f"{count} {one if count == 1 else many}"


def _listed(nodes: list[str], limit: int = 5) -> str:
    """The first node ids, and how many more there were, so a reason stays one line."""
    shown = ", ".join(nodes[:limit])
    rest = len(nodes) - limit
    return f"{shown} e altri {rest}" if rest > 0 else shown


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
