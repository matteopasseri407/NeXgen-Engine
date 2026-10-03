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


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    commands = [
        ([sys.executable, str(root / "03-INFRA/scripts/ruff_baseline_check.py")],
         {**env, "NEXGEN_RUFF_ENTRY_DIR": str(root / "03-INFRA/scripts")}),
        ([sys.executable, "-m", "pytest", *sys.argv[1:]], env),
    ]
    for command, command_env in commands:
        result = subprocess.run(command, cwd=root, env=command_env, check=False)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
