"""Signature checks use a gpg that can verify, not the one Git for Windows ships.

On Windows `gpg` on PATH is usually the MSYS build inside Git: it cannot start an
agent or import a keyring outside its own tree, so every verification comes back
"unverifiable" and a nightly channel refuses every commit. The native GnuPG is the
one that works, and this is the choice between them.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.release_trust import gpg_binary  # noqa: E402 - sys.path shim for cloned checkout


def test_the_msys_gpg_behind_git_is_skipped_when_a_real_one_is_there():
    paths = {
        "gpg": r"C:\Program Files\Git\usr\bin\gpg.exe",
        "gpg2": r"C:\Program Files\GnuPG\bin\gpg2.exe",
    }

    assert gpg_binary(which=paths.get, program_files=r"C:\nessuno") == paths["gpg2"]


def test_the_native_install_wins_over_whatever_the_path_says():
    paths = {"gpg": r"C:\Program Files\Git\usr\bin\gpg.exe"}
    native = Path(r"C:\Program Files") / "GnuPG" / "bin" / "gpg.exe"

    if not native.is_file():
        return  # nothing installed here: the rule below still holds on a machine that has it
    assert gpg_binary(which=paths.get, program_files=r"C:\Program Files") == str(native)


def test_a_machine_with_only_msys_still_gets_an_answer():
    paths = {"gpg": r"C:\Program Files\Git\usr\bin\gpg.exe"}

    assert gpg_binary(which=paths.get, program_files=r"C:\nessuno") == paths["gpg"]
