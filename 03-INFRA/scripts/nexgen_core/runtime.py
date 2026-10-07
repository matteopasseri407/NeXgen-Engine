"""The Python environment the engine's own commands run under.

The launchers used to start whatever `python3` was first on PATH. That interpreter
has PyYAML, which is all the guard needs, and none of the libraries the rest of
the engine declares as core: LangGraph for the Council relay and the local lane,
LangChain for its model calls, `mcp` for the servers. So on a machine where nobody
had installed them by hand the Council's resumable relay answered "missing
dependency" and the `drive` server came up with no tools, and nothing said why.

Now the engine provisions its own environment, outside every git tree (the updater
refuses a dirty checkout and counts untracked files), from the dependency list in
`pyproject.toml` (one list, not a second one that could drift). The launchers use it
once it is there and fall back to any suitable Python until then, so the recovery
path (the guard, the doctor) still needs nothing but PyYAML.

An engine installed as a package has no checkout to provision from and does not
need one: its own environment already carries the dependencies, and `check` says
so if it does not.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import time
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from nexgen_core.errors import NexgenError
from nexgen_core.files import atomic_write_text

#: Importable module -> the distribution that provides it. The first column is
#: what is checked, the second what a person is told to install.
ESSENTIAL_IMPORTS: Mapping[str, str] = {
    "yaml": "PyYAML",
    "langgraph": "langgraph",
    "langgraph.checkpoint.sqlite": "langgraph-checkpoint-sqlite",
    "langchain_ollama": "langchain-ollama",
    "httpx": "httpx",
    "mcp": "mcp",
}

#: Present only when the environment was provisioned and verified. The launchers
#: test for this file, so they never have to start an interpreter to decide.
STAMP_NAME = ".provisioned"
PIP_TIMEOUT_SECONDS = 900.0
STEP_TIMEOUT_SECONDS = 120.0

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


class RuntimeProvisionError(NexgenError):
    """The engine's environment could not be created, filled or verified."""


@dataclass(frozen=True)
class RuntimeResult:
    changed: bool
    detail: str


def runtime_python(runtime_dir: Path) -> Path:
    if sys.platform == "win32":
        return runtime_dir / "Scripts" / "python.exe"
    return runtime_dir / "bin" / "python"


def missing_imports(modules: Sequence[str] | None = None) -> list[str]:
    """The essential modules the *running* interpreter cannot import."""
    missing: list[str] = []
    for module in modules or ESSENTIAL_IMPORTS:
        try:
            found = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):  # a missing parent package raises for a dotted name
            found = False
        if not found:
            missing.append(module)
    return missing


def declared_dependencies(engine_clone: Path) -> list[str]:
    """The `[project] dependencies` of the checkout, which is the only list there is."""
    pyproject = engine_clone / "pyproject.toml"
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        deps = data["project"]["dependencies"]
    except (OSError, ValueError, KeyError) as exc:
        raise RuntimeProvisionError(f"cannot read the engine's dependency list from {pyproject}: {exc}") from exc
    if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
        raise RuntimeProvisionError(f"{pyproject}: [project] dependencies is not a list of strings")
    return list(deps)


def fingerprint(dependencies: Sequence[str]) -> str:
    """What the environment was built from: the dependencies and the interpreter's minor version."""
    text = "\n".join(sorted(dependencies)) + f"\npython={sys.version_info.major}.{sys.version_info.minor}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _read_stamp(runtime_dir: Path) -> dict:
    try:
        data = json.loads((runtime_dir / STAMP_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def is_current(runtime_dir: Path, dependencies: Sequence[str]) -> bool:
    """Provisioned from these dependencies, and its interpreter is still there."""
    return (
        runtime_python(runtime_dir).exists()
        and _read_stamp(runtime_dir).get("fingerprint") == fingerprint(dependencies)
    )


def _tail(proc: subprocess.CompletedProcess[str]) -> str:
    text = (proc.stderr or proc.stdout or "").strip().splitlines()
    return " | ".join(text[-3:])[:400] if text else "no output"


def verify(python: Path, *, run: Runner = subprocess.run) -> list[str]:
    """The essential modules `python` cannot import (asked of that interpreter, not of this one)."""
    code = (
        "import importlib.util as u\n"
        f"mods = {list(ESSENTIAL_IMPORTS)!r}\n"
        "def has(m):\n"
        "    try:\n        return u.find_spec(m) is not None\n"
        "    except (ImportError, ValueError):\n        return False\n"
        "print(','.join(m for m in mods if not has(m)))\n"
    )
    proc = run([str(python), "-c", code], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=STEP_TIMEOUT_SECONDS, check=False)
    if proc.returncode != 0:
        raise RuntimeProvisionError(f"the provisioned interpreter does not run: {_tail(proc)}")
    return [m for m in proc.stdout.strip().split(",") if m]


def ensure_runtime(
    runtime_dir: Path,
    engine_clone: Path,
    *,
    base_python: str | None = None,
    run: Runner = subprocess.run,
    log: Callable[[str], None] = print,
) -> RuntimeResult:
    """Creates or repairs the engine's environment. Idempotent: current means untouched.

    Raises RuntimeProvisionError with the cause and what to do about it; never
    leaves a stamp behind unless the imports were verified in the new environment.
    """
    dependencies = declared_dependencies(engine_clone)
    python = runtime_python(runtime_dir)
    if is_current(runtime_dir, dependencies) and not verify(python, run=run):
        return RuntimeResult(False, "already provisioned")

    if not python.exists():
        runtime_dir.parent.mkdir(parents=True, exist_ok=True)
        log(f"runtime: creating {runtime_dir}")
        made = run([base_python or sys.executable, "-m", "venv", "--clear", str(runtime_dir)],
                   capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=STEP_TIMEOUT_SECONDS, check=False)
        if made.returncode != 0:
            raise RuntimeProvisionError(
                f"could not create the engine environment ({_tail(made)}). "
                "On Debian or Ubuntu this usually means the python3-venv package is not installed."
            )
    # A stamp from an older provisioning must not vouch for a half-rebuilt environment.
    (runtime_dir / STAMP_NAME).unlink(missing_ok=True)

    log(f"runtime: installing {len(dependencies)} dependencies")
    installed = run(
        [str(python), "-m", "pip", "install", "--disable-pip-version-check", "--timeout", "30", "--retries", "2",
         *dependencies],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=PIP_TIMEOUT_SECONDS, check=False,
    )
    if installed.returncode != 0:
        raise RuntimeProvisionError(
            f"pip could not install the engine's dependencies ({_tail(installed)}). "
            "Check the network and rerun 'nexgen runtime ensure'."
        )
    still_missing = verify(python, run=run)
    if still_missing:
        raise RuntimeProvisionError(
            "the engine environment was built but cannot import: " + ", ".join(still_missing)
        )
    atomic_write_text(
        runtime_dir / STAMP_NAME,
        json.dumps({"fingerprint": fingerprint(dependencies), "dependencies": sorted(dependencies),
                    "python": sys.version.split()[0], "at": time.time()}, indent=2) + "\n",
    )
    return RuntimeResult(True, f"provisioned with {len(dependencies)} dependencies")
