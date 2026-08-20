#!/usr/bin/env python3
"""Launcher per AI Council — NeXgen Engine v2."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    """Inoltra l'invocazione al modulo canonico council.py."""
    if argv is None:
        argv = sys.argv[1:]

    engine_root = Path(os.environ.get("AGENT_ENGINE_ROOT") or Path(__file__).resolve().parents[3])
    council_py = engine_root / "agent-universal-layer" / "council" / "council.py"

    if not council_py.is_file():
        # Fallback percorso relativo locale
        council_py = Path(__file__).resolve().parents[2] / "agent-universal-layer" / "council" / "council.py"

    if not council_py.is_file():
        print(f"[ERRORE] Orchestratore AI Council non trovato in {council_py}", file=sys.stderr)
        return 1

    res = subprocess.run([sys.executable, str(council_py), *argv])
    return res.returncode


if __name__ == "__main__":
    sys.exit(main())
