"""Regression coverage for the cross-platform file-manager launcher."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "03-INFRA" / "scripts"


@pytest.mark.skipif(os.name == "nt", reason="POSIX launcher behavior is covered on Linux and macOS.")
def test_posix_launcher_rejects_relative_folder_without_opening_a_window():
    launcher = SCRIPTS / "agent-open-folder.sh"
    result = subprocess.run(
        [str(launcher), "relative-folder"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "percorso assoluto" in result.stderr


def test_windows_launcher_uses_explorer_and_rejects_relative_paths():
    launcher = (SCRIPTS / "agent-open-folder.ps1").read_text(encoding="utf-8")

    assert "[System.IO.Path]::IsPathRooted($Path)" in launcher
    assert "Test-Path -LiteralPath $Folder -PathType Container" in launcher
    # The folder is opened via Invoke-Item -LiteralPath (ShellExecute, no
    # command-line parsing): paths with spaces, drive roots and trailing
    # backslashes all work without the quoting minefield of a hand-built
    # explorer.exe command line (2026-08-15 review, councils of Opus 5).
    # Substring on the call shape, not an exact-string assertion: this
    # pins the "open without command-line parsing" behavior, which is the
    # substance that matters, and survives innocuous argument changes.
    assert "Invoke-Item -LiteralPath $Folder" in launcher
