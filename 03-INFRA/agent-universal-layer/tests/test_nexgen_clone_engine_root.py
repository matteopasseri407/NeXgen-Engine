"""A clone outside ~/.nexgen-engine is the engine its commands and its install use.

Seen on a clean Ubuntu 24.04 that followed the README (clone into
~/KnowledgeVault): the launcher ran the clone's code, but every command resolved
the engine root to ~/.nexgen-engine/03-INFRA, which does not exist there. The
guard's `modules` phase failed and the units the install wrote named that folder.
Both scripts are run here with `sh`, the way a person runs them.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import shims  # noqa: E402 - sys.path shim for cloned checkout

REPO = Path(__file__).resolve().parents[3]
PRINT_ROOT = "import os\nprint(os.environ.get('AGENT_ENGINE_ROOT', '<unset>'))\n"
POSIX_ONLY = sys.platform == "win32" or shutil.which("sh") is None


def _clone(base: Path, entry: str) -> Path:
    """A clone whose Python `entry` prints the engine root it was handed."""
    clone = base / "KnowledgeVault"
    script = clone / "03-INFRA" / "scripts" / "nexgen_core" / entry
    script.parent.mkdir(parents=True)
    script.write_text(PRINT_ROOT, encoding="utf-8")
    return clone


def _env(home: Path, **extra: str) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key != "AGENT_ENGINE_ROOT"}
    return {**env, "HOME": str(home), **extra}


def _sh(script: Path, env: dict[str, str]) -> str:
    result = subprocess.run(["sh", str(script)], env=env, capture_output=True, text=True, encoding="utf-8",
                            timeout=60, check=True)
    return result.stdout.strip().splitlines()[-1]


@pytest.mark.skipif(POSIX_ONLY, reason="the POSIX launcher runs under sh")
def test_the_launcher_hands_its_own_clone_as_the_engine_root(tmp_path):
    clone = _clone(tmp_path, "cli/__init__.py")
    launcher = tmp_path / "nexgen"
    entry = clone / "03-INFRA" / "scripts" / "nexgen_core" / "cli" / "__init__.py"
    launcher.write_text(shims._render("nexgen", [], entry, False, tmp_path / "no-runtime"), encoding="utf-8")

    assert _sh(launcher, _env(tmp_path)) == str(clone / "03-INFRA")


@pytest.mark.skipif(POSIX_ONLY, reason="the installer runs under sh")
def test_the_installer_names_its_clone_unless_an_engine_is_installed(tmp_path):
    clone = _clone(tmp_path, "bootstrap.py")
    shutil.copy(REPO / "install.sh", clone / "install.sh")

    assert _sh(clone / "install.sh", _env(tmp_path)) == str(clone / "03-INFRA")

    (tmp_path / ".nexgen-engine" / "03-INFRA").mkdir(parents=True)
    assert _sh(clone / "install.sh", _env(tmp_path)) == "<unset>"
    assert _sh(clone / "install.sh", _env(tmp_path, AGENT_ENGINE_ROOT="/declared")) == "/declared"
