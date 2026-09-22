"""NeXgen Engine Core Package (v2).

Modular, cross-platform (Linux & Windows) architecture for the agent layer.
"""
from __future__ import annotations

from pathlib import Path


def _read_version() -> str:
    """The VERSION file at the repo root is the single source; packaging
    (`pyproject.toml` dynamic version) and the updater already read it.
    A hardcoded duplicate here drifted before (0.99.1 vs 2.0.0), so this
    module reads the same file instead of declaring its own number. When
    the package runs from an installed wheel without the checkout around,
    the importlib metadata (built from that same VERSION file) is the
    fallback; "unknown" only when neither is reachable, never a guess."""
    try:
        root = Path(__file__).resolve().parents[3]
        value = (root / "VERSION").read_text(encoding="utf-8").strip()
        if value:
            return value
    except OSError:
        pass
    try:
        from importlib.metadata import version

        return version("nexgen-engine")
    except Exception:
        return "unknown"


__version__ = _read_version()
