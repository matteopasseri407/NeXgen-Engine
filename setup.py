"""Build hook: the wheel carries the engine layer.

`pyproject.toml` declares the two Python packages. The rest of the engine (the
Council, lazy-mcp, the hooks, the manifests, the skills, the leak-scan and the deploy
templates) lives in `03-INFRA/agent-universal-layer`, outside any package, so a wheel
used to ship the commands and none of what they run: `nexgen council` forwarded to a
folder that exists only in a clone. This copies the layer into the wheel as
`nexgen_core/_engine/agent-universal-layer`, which is where `paths.resolve_engine_root`
looks when there is no checkout.

It fails the build, rather than quietly shipping a wheel without the layer, which is the
defect it exists to prevent. Editable installs run from the repository and need no copy.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py

ROOT = Path(__file__).resolve().parent
LAYER = ROOT / "03-INFRA" / "agent-universal-layer"
IGNORE = shutil.ignore_patterns("tests", "__pycache__", "*.pyc", "*.pyo", ".pytest_cache")


class build_py(_build_py):
    def run(self) -> None:
        super().run()
        if getattr(self, "editable_mode", False):
            return
        if not LAYER.is_dir():
            raise SystemExit(f"cannot build: the engine layer is missing at {LAYER} (is the sdist incomplete?)")
        destination = Path(self.build_lib) / "nexgen_core" / "_engine" / "agent-universal-layer"
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(LAYER, destination, ignore=IGNORE)


setup(cmdclass={"build_py": build_py})
