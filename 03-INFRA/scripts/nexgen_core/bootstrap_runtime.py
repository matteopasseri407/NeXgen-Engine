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
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from nexgen_core.i18n import t
from nexgen_core.paths import resolve_runtime_dir
from nexgen_core.runtime import RuntimeProvisionError, ensure_runtime, missing_imports, runtime_python

#: Set on the environment the re-executed bootstrap inherits. Without it a
#: machine whose environment still cannot import PyYAML would provision forever.
REEXEC_MARKER = "NEXGEN_BOOTSTRAP_REEXEC"

#: Set on the command run again after its environment was repaired, so a library that
#: still does not import is reported by the command instead of repaired forever.
HEAL_MARKER = "NEXGEN_RUNTIME_HEALED"

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

    It is not only PyYAML: a Python that already has some of the declared libraries
    (a developer's, or one an earlier tool filled) passed this step, built nothing,
    and the install then died on the first library it lacked (filelock, on 3.0).
    """
    absent = [] if not yaml_available() else missing_imports()
    if yaml_available() and not absent:
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
            return _reexec(python, argv)
        return None
    if absent:
        print(t("The engine's environment lacks {names}: installing the engine's dependency list "
                "into {dir}, then running the command again.", names=", ".join(absent), dir=runtime_dir))
    else:
        print(t("PyYAML is missing: the engine is creating its own Python environment in {dir} "
                "(a few minutes, once per machine).", dir=runtime_dir))
    try:
        ensure_runtime(runtime_dir, clone)
    except RuntimeProvisionError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return _reexec(python, argv)


def _reexec(python: Path, argv: list[str]) -> int | None:
    """This same bootstrap, again, in the engine's interpreter; marked so it cannot loop."""
    os.environ[REEXEC_MARKER] = "1"
    command = [str(python), str(BOOTSTRAP_SCRIPT), *argv]
    if os.name == "nt":
        # Windows has no exec: os.execv starts the child and ends this process at once,
        # so install.ps1 got exit 0 back while the install was still running beside it.
        return subprocess.call(command)
    os.execv(str(python), command)
    return None


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


def heal_running_environment(
    argv: Sequence[str],
    *,
    entry: str,
    environ: Mapping[str, str] = os.environ,
    prefix: str = sys.prefix,
    missing: Callable[[], list[str]] = missing_imports,
    ensure: Callable[..., object] = ensure_runtime,
    call: Callable[..., int] = subprocess.call,
) -> int | None:
    """Repairs the engine's own environment when the code it runs needs a library it lacks.

    An update merges the new tree first and provisions the environment afterwards, and
    between the two it runs the new tree's own commands (publishing the engine pin is
    the first). A release that adds a dependency therefore met an environment without it
    and rolled itself back, and the updater that does this is the *previous* release's,
    so only the new tree can fix it: when this interpreter is the engine's environment
    and a declared library is missing, install the declared list into it and run the
    same command again in a fresh process. Returns that command's exit code, or None to
    carry on. Any other interpreter (a test venv, CI, an installed package) is left alone.
    """
    if environ.get(HEAL_MARKER) or list(argv[:1]) == ["runtime"]:
        return None
    runtime_dir = resolve_runtime_dir()
    try:
        own = Path(prefix).resolve() == runtime_dir.resolve()
    except OSError:  # nexgen: allow reason="a runtime path that cannot be resolved is not this interpreter: nothing to repair"
        return None
    clone = _engine_clone()
    if not own or clone is None:
        return None
    absent = missing()
    if not absent:
        return None
    print(t("The engine's environment lacks {names}: installing the engine's dependency list "
            "into {dir}, then running the command again.", names=", ".join(absent), dir=runtime_dir),
          file=sys.stderr)
    try:
        ensure(runtime_dir, clone, log=lambda line: print(line, file=sys.stderr))
    except RuntimeProvisionError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return call([sys.executable, entry, *argv], env={**environ, HEAL_MARKER: "1"})
