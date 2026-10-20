"""The real-flow smoke: this checkout's own commands, run for real, read-only.

The test suite proves the engine against fakes, on a machine that may have
nothing installed. This proves the opposite on the machine that pushes: the
entry points start, answer and return the exit codes they promise, and the
launcher this platform really gets is rendered from this checkout and run.

Every command here only reads; the launcher check writes one launcher into a
temporary folder. The run has a budget because the pre-push hook runs it: over
BUDGET_SECONDS it fails and says so, rather than quietly costing minutes.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import time
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


def check_commands() -> list[str]:
    """Run every read-only command: the reasons each one failed, empty when all of them answered."""
    problems: list[str] = []
    for args, expected in CASES:
        name = "nexgen " + " ".join(args)
        try:
            done = run([sys.executable, str(ENTRY), *args])
        except (subprocess.TimeoutExpired, OSError) as err:
            problems.append(f"{name}: no answer in {COMMAND_TIMEOUT}s ({err})")
            _report(name, False, f"no answer in {COMMAND_TIMEOUT}s")
            continue
        crashed = "Traceback (most recent call last)" in done.stderr
        good = done.returncode in expected and not crashed
        tail = (done.stdout.strip().splitlines() or [""])[-1][:60]
        if not _report(name, good, f"rc={done.returncode} {tail}"):
            problems.append(f"{name}: exit {done.returncode}, expected one of {sorted(expected)}")
    return problems


def _failure_tail(done: subprocess.CompletedProcess, limit: int = 5) -> str:
    """The last lines a failed command wrote: stderr, or stdout when stderr is empty."""
    text = done.stderr if done.stderr.strip() else done.stdout
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    return " | ".join(lines[-limit:])


def check_launcher() -> list[str]:
    """The launcher this platform really gets, rendered from this checkout and run twice.

    A command that fails must not read as success: that failure mode shipped once
    (every `.cmd` launcher exited 0), so the exit code is checked here, not assumed.
    """
    from nexgen_core import shims

    problems: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "home"
        installed = shims.install_shims(scripts_dir=SCRIPTS, bin_dir=Path(tmp) / "bin", home=home)
        launcher = Path(installed[0])
        runner = [str(launcher)] if sys.platform == "win32" else ["sh", str(launcher)]
        for args, want_nonzero in ((("--version",), False), (("no-such-command",), True)):
            name = f"launcher {' '.join(args)}"
            try:
                done = run([*runner, *args])
            except (subprocess.TimeoutExpired, OSError) as err:
                problems.append(f"{name}: no answer in {COMMAND_TIMEOUT}s ({err})")
                _report(name, False, f"no answer in {COMMAND_TIMEOUT}s")
                continue
            good = (done.returncode != 0) == want_nonzero
            said = "" if good else _failure_tail(done)
            detail = f"rc={done.returncode}" + (f": {said}" if said else "")
            if not _report(name, good, detail):
                expected = "a failure" if want_nonzero else "success"
                problems.append(f"{name}: exit {done.returncode}, expected {expected}" + (f": {said}" if said else ""))
    return problems


def main() -> int:
    # A Windows console or pipe is cp1252: a child's undecodable byte must not crash the report.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    started = time.monotonic()
    problems = check_commands() + check_launcher()
    spent = time.monotonic() - started
    if spent > BUDGET_SECONDS:
        problems.append(f"the smoke took {spent:.0f}s, over its {BUDGET_SECONDS}s budget: the hook runs it on every push")
    print(f"SMOKE {'OK' if not problems else 'FAILED'} ({spent:.0f}s)")
    for problem in problems:
        print(f"  {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
