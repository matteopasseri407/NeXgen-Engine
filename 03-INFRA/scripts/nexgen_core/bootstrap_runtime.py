"""The engine's own Python environment, created before the prerequisites are read.

This checkout's bootstrap used to stop on a clean machine with "PyYAML — pip
install pyyaml". On Ubuntu 24.04 that remedy does not exist: the system pip
refuses to install outside a virtual environment (PEP 668), so the install had
no way forward. The engine already builds its own environment from
`pyproject.toml` and the launchers already prefer it once it is there
(`nexgen_core.runtime`); this module is what makes the first run use it too,
before anything reads YAML.

The re-executed bootstrap carries `REEXEC_MARKER` so a second miss cannot loop:
with the marker set the prerequisites report the failure and the install stops.
`--check` writes nothing and so provisions nothing.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

from nexgen_core.i18n import t
from nexgen_core.paths import resolve_runtime_dir
from nexgen_core.runtime import RuntimeProvisionError, ensure_runtime, runtime_python

#: Set on the environment the re-executed bootstrap inherits. Without it a
#: machine whose environment still cannot import PyYAML would provision forever.
REEXEC_MARKER = "NEXGEN_BOOTSTRAP_REEXEC"

#: The bootstrap to run again: the same entry point, in the interpreter we built.
BOOTSTRAP_SCRIPT = Path(__file__).resolve().with_name("bootstrap.py")


def yaml_available() -> bool:
    """Whether this interpreter can import PyYAML, the one dependency the guard needs."""
    return importlib.util.find_spec("yaml") is not None


def _engine_clone() -> Path | None:
    """The checkout holding `pyproject.toml`, or None for an installed package.

    An installed engine has no checkout to provision from: its own environment
    already carries the dependencies, and its launchers live in the wheel.
    """
    clone = Path(__file__).resolve().parents[3]
    return clone if (clone / "pyproject.toml").is_file() else None


def provision_before_prerequisites(argv: list[str], *, check: bool = False) -> int | None:
    """Builds the engine's environment and re-executes into it, or says why not.

    Returns the exit code when the bootstrap must stop here, and None when it can
    carry on with the interpreter it is already running on.
    """
    if yaml_available():
        return None
    clone = _engine_clone()
    if clone is None or os.environ.get(REEXEC_MARKER):
        # Nothing to provision from, or we are already the re-executed run that
        # still cannot import it: the prerequisites report it, as they always did.
        return None
    runtime_dir = resolve_runtime_dir()
    python = runtime_python(runtime_dir)
    if check:
        # Reading is allowed: an environment an earlier install built answers the
        # whole plan. Building one is not, so without it the plan stays partial.
        if python.is_file():
            _reexec(python, argv)
        return None
    print(t("PyYAML is missing: the engine is creating its own Python environment in {dir} "
            "(a few minutes, once per machine).", dir=runtime_dir))
    try:
        ensure_runtime(runtime_dir, clone)
    except RuntimeProvisionError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    _reexec(python, argv)
    return None


def _reexec(python: Path, argv: list[str]) -> None:
    """This same bootstrap, again, in the engine's interpreter; marked so it cannot loop."""
    os.environ[REEXEC_MARKER] = "1"
    os.execv(str(python), [str(python), str(BOOTSTRAP_SCRIPT), *argv])


def role_plan_ready(check: bool) -> bool:
    """Whether the role step can run here, or the one line that says why it waits.

    The role code reads YAML. A `--check` on a clean machine has no environment
    to borrow and may not build one: it says so instead of failing on an import.
    """
    if not check or yaml_available():
        return True
    print("\n" + t("The role's detailed plan needs the engine's Python environment, which the install "
                   "creates first: run it once without --check, then --check shows the whole plan."))
    return False
