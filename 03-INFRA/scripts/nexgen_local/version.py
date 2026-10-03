"""Single owner for the engine version string: installed or cloned."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def engine_version(engine_root: Path | None = None) -> str:
    try:
        return version("nexgen-engine")
    except PackageNotFoundError:
        from .config import default_engine_root

        root = engine_root or default_engine_root()
        version_file = root / "VERSION"
        return version_file.read_text().strip() if version_file.is_file() else "sconosciuta"
