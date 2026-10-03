"""Single owner for the engine version string: installed or cloned."""

from __future__ import annotations

from pathlib import Path


def engine_version(engine_root: Path | None = None) -> str:
    try:
        from importlib.metadata import version

        return version("nexgen-engine")
    except Exception:  # noqa: BLE001 - cloned checkout without packaging
        from nexgen_core.paths import default_engine_root

        root = engine_root or default_engine_root()
        version_file = root / "VERSION"
        return version_file.read_text().strip() if version_file.is_file() else "sconosciuta"
