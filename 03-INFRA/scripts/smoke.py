"""The real-flow smoke: this checkout's own commands, run for real, read-only.

The test suite proves the engine against fakes, on a machine that may have
nothing installed. This proves the opposite on the machine that pushes: the
entry points start, answer and return the exit codes they promise, and the
launcher this platform really gets is rendered from this checkout and run.

Every command here only reads; the launcher check writes one launcher into a
temporary folder. The commands are independent, so they run at the same time:
the hook waits for the slowest of them instead of for their sum, which is what
made the same smoke pass on a quiet machine and fail on one that was working.
The run keeps a budget regardless, because the pre-push hook runs it: over
BUDGET_SECONDS it fails and says so, rather than quietly costing minutes.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BUDGET_SECONDS = 30
COMMAND_TIMEOUT = 20
SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parents[1]
ENTRY = SCRIPTS / "nexgen_core" / "cli" / "__init__.py"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

#: Read-only command, and the exit codes that mean "ran correctly": doctor
#: reports a real FAIL as 1, and argparse refuses an unknown command with 2.
CASES: tuple[tuple[tuple[str, ...], frozenset[int]], ...] = (
    (("--version",), frozenset({0})),
    (("doctor", "--summary"), frozenset({0, 1})),
    (("plan",), frozenset({0})),
    (("preflight",), frozenset({0})),
    (("inventory",), frozenset({0})),
    (("config", "mirrors"), frozenset({0})),
    (("no-such-command",), frozenset({2})),
)


def run(argv: list[str]) -> subprocess.CompletedProcess:
    """One command, with its own timeout and no inherited stdin (a child that reads one hangs)."""
    return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=COMMAND_TIMEOUT, stdin=subprocess.DEVNULL)


def _report(name: str, good: bool, detail: str) -> bool:
    print(f"{'OK  ' if good else 'FAIL'} {name:<32} {detail}")
    return good


def _one_command(case: tuple[tuple[str, ...], frozenset[int]]) -> tuple[str, bool, str, str]:
    """Run one read-only command and say what it did. No printing: order is decided later."""
    args, expected = case
    name = "nexgen " + " ".join(args)
    try:
        done = run([sys.executable, str(ENTRY), *args])
    except (subprocess.TimeoutExpired, OSError) as err:
        return name, False, f"no answer in {COMMAND_TIMEOUT}s", f"{name}: no answer in {COMMAND_TIMEOUT}s ({err})"
    crashed = "Traceback (most recent call last)" in done.stderr
    good = done.returncode in expected and not crashed
    tail = (done.stdout.strip().splitlines() or [""])[-1][:60]
    problem = "" if good else f"{name}: exit {done.returncode}, expected one of {sorted(expected)}"
    return name, good, f"rc={done.returncode} {tail}", problem


def check_commands() -> list[str]:
    """Run every read-only command at the same time: the reasons any failed, empty when all answered.

    The hook pays the slowest of them, not their sum. They are read-only and
    independent by contract — that is what makes them safe to run for real in
    the first place — and the report keeps the order they are listed in, so the
    same smoke reads the same way twice.
    """
    with ThreadPoolExecutor(max_workers=len(CASES)) as pool:
        results = list(pool.map(_one_command, CASES))
    problems: list[str] = []
    for name, good, detail, problem in results:
        _report(name, good, detail)
        if problem:
            problems.append(problem)
    return problems


def _failure_tail(done: subprocess.CompletedProcess, limit: int = 5) -> str:
    """The last lines a failed command wrote: stderr, or stdout when stderr is empty."""
    text = done.stderr if done.stderr.strip() else done.stdout
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    return " | ".join(lines[-limit:])


def _one_launcher_run(runner: list[str], args: tuple[str, ...], want_nonzero: bool) -> tuple[str, bool, str, str]:
    """One launcher invocation: what it did, and the reason it is wrong when it is."""
    name = f"launcher {' '.join(args)}"
    try:
        done = run([*runner, *args])
    except (subprocess.TimeoutExpired, OSError) as err:
        return name, False, f"no answer in {COMMAND_TIMEOUT}s", f"{name}: no answer in {COMMAND_TIMEOUT}s ({err})"
    good = (done.returncode != 0) == want_nonzero
    said = "" if good else _failure_tail(done)
    detail = f"rc={done.returncode}" + (f": {said}" if said else "")
    expected = "a failure" if want_nonzero else "success"
    problem = "" if good else f"{name}: exit {done.returncode}, expected {expected}" + (f": {said}" if said else "")
    return name, good, detail, problem


def check_launcher() -> list[str]:
    """The launcher this platform really gets, rendered from this checkout and run twice.

    A command that fails must not read as success: that failure mode shipped once
    (every `.cmd` launcher exited 0), so the exit code is checked here, not assumed.
    The two runs are independent, so they go together for the same reason the
    commands do.
    """
    from nexgen_core import shims

    problems: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "home"
        installed = shims.install_shims(scripts_dir=SCRIPTS, bin_dir=Path(tmp) / "bin", home=home)
        launcher = Path(installed[0])
        runner = [str(launcher)] if sys.platform == "win32" else ["sh", str(launcher)]
        runs = [(("--version",), False), (("no-such-command",), True)]
        with ThreadPoolExecutor(max_workers=len(runs)) as pool:
            results = list(pool.map(lambda case: _one_launcher_run(runner, *case), runs))
        for name, good, detail, problem in results:
            _report(name, good, detail)
            if problem:
                problems.append(problem)
    return problems


def main() -> int:
    # A Windows console or pipe is cp1252: a child's undecodable byte must not crash the report.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    started = time.monotonic()
    # The two groups are independent too, and the launcher's two starts are the
    # slowest pair after the doctor itself: running the groups together keeps the
    # hook's wait at the slowest group instead of their sum. Each group prints
    # its own block, so the report still reads in one order per group.
    with ThreadPoolExecutor(max_workers=2) as pool:
        commands = pool.submit(check_commands)
        launcher = pool.submit(check_launcher)
        problems = commands.result() + launcher.result()
    spent = time.monotonic() - started
    if spent > BUDGET_SECONDS:
        problems.append(f"the smoke took {spent:.0f}s, over its {BUDGET_SECONDS}s budget: the hook runs it on every push")
    print(f"SMOKE {'OK' if not problems else 'FAILED'} ({spent:.0f}s)")
    for problem in problems:
        print(f"  {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
