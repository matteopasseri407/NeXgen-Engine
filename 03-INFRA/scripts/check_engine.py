#!/usr/bin/env python3
"""Contributor gate: the existing Ruff baseline, then sandboxed pytest.

Optional arguments are forwarded to pytest, for example a test file or -k.
Without arguments it runs the complete suite. It does not sync or release.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _resolve_python(root: Path) -> str:
    """Uses sys.executable if it can import dependencies, or falls back to repo .venv."""
    try:
        import httpx  # noqa: F401
        import pytest  # noqa: F401
        return sys.executable
    except ImportError:
        pass
    for venv_python in (
        root / ".venv" / "bin" / "python",
        root / ".venv" / "Scripts" / "python.exe",
        root / ".venv" / "bin" / "python3",
    ):
        if venv_python.is_file() and os.access(venv_python, os.X_OK):
            return str(venv_python)
    return sys.executable


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    python_bin = _resolve_python(root)
    env = dict(os.environ)
    env["PATH"] = str(Path(python_bin).parent) + os.pathsep + env.get("PATH", "")
    commands = [
        ([python_bin, str(root / "03-INFRA/scripts/ruff_baseline_check.py")],
         {**env, "NEXGEN_RUFF_ENTRY_DIR": str(root / "03-INFRA/scripts")}),
        ([python_bin, "-m", "pytest", *sys.argv[1:]], env),
    ]
    for command, command_env in commands:
        result = subprocess.run(command, cwd=root, env=command_env, check=False)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
