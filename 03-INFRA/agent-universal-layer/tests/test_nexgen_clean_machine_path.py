"""The path a clean machine walks: install, first sync, status, in one go.

`bootstrap --local` is the whole offline install (scaffold, profile, remotes,
launcher shims, the first alignment and the doctor's verdict) and it returns 0
only when the doctor finds nothing left. Running it on a fresh clone of the
project exercises the product as a user gets it, not each unit alone: this is
the end-of-phase-1 canary that catches a change breaking the real path.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.paths import resolve_state_dir  # noqa: E402

LAUNCHER = "nexgen.cmd" if os.name == "nt" else "nexgen"


def _fresh_clone(tmp_path: Path) -> Path:
    """The vault of a machine with nothing on it: a clone of the project."""
    vault = tmp_path / "vault"
    subprocess.run(["git", "clone", "--quiet", str(REPO_ROOT), str(vault)], check=True, capture_output=True)
    for key, value in (("user.email", "clean-machine"), ("user.name", "Clean Machine")):
        subprocess.run(["git", "-C", str(vault), "config", key, value], check=True, capture_output=True)
    return vault


def _clean_env(home: Path, vault: Path) -> dict[str, str]:
    """A disposable machine: attributes and state stay under `home`."""
    return {
        "HOME": str(home),
        "USERPROFILE": str(home),
        "APPDATA": str(home / "AppData" / "Roaming"),
        "LOCALAPPDATA": str(home / "AppData" / "Local"),
        "KNOWLEDGE_VAULT_PATH": str(vault),
        # A single-machine install runs the engine from the clone itself.
        "AGENT_ENGINE_ROOT": str(vault / "03-INFRA"),
        "NEXGEN_DISABLE_HOST_MUTATIONS": "1",
    }


def _machine_files(home: Path) -> dict[str, str]:
    """Hash of every file under `home`, minus the state directory: it logs a cycle each time."""
    state = resolve_state_dir(home)
    out: dict[str, str] = {}
    for path in sorted(home.rglob("*")):
        if path.is_file() and state not in path.parents:
            out[str(path.relative_to(home))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def test_a_clean_machine_installs_syncs_and_reports_ready(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    vault = _fresh_clone(tmp_path)
    for key, value in _clean_env(home, vault).items():
        monkeypatch.setenv(key, value)

    from nexgen_core import bootstrap

    assert bootstrap.main(["--local", "--root", str(vault)]) == 0, "the install did not reach Ready"

    launcher = home / ".local" / "bin" / LAUNCHER
    assert launcher.is_file(), "the install left no launcher on PATH"
    liveness = resolve_state_dir(home) / "agent-guard-liveness"
    assert liveness.is_file(), "the first sync did not record a cycle"
    assert "FAIL=" not in liveness.read_text(encoding="utf-8"), "a phase failed during the first sync"

    # The second pass over an installed machine is a fixed point: a rewrite
    # here is a backup, an mtime, a reload, every 30 minutes, forever.
    before = _machine_files(home)
    assert bootstrap.main(["--local", "--root", str(vault)]) == 0
    assert _machine_files(home) == before, "the second install rewrote the machine"
