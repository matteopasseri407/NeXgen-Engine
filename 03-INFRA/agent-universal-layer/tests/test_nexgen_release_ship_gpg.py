"""Test that release_ship detects MSYS gpg on Windows and stops with a PowerShell message."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from nexgen_core import release_ship
from nexgen_core.release_gpg import MsysGpgError, check_windows_msys_gpg
from nexgen_core.release_ship import Shipper, ShipStop


def test_detects_msys_gpg_in_git_config(tmp_path: Path) -> None:
    def fake_run(argv, cwd):
        if list(argv[:3]) == ["git", "config", "--get"]:
            return subprocess.CompletedProcess(argv, 0, stdout="/usr/bin/gpg\n", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    with pytest.raises(MsysGpgError) as exc_info:
        check_windows_msys_gpg(fake_run, tmp_path, platform="win32", env={})
    assert "PowerShell" in str(exc_info.value)
    assert "MSYS" in str(exc_info.value) or "msys" in str(exc_info.value).lower()


def test_detects_msys_gpg_from_gpg_version_when_no_config(tmp_path: Path) -> None:
    def fake_run(argv, cwd):
        if list(argv[:3]) == ["git", "config", "--get"]:
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="")
        if argv[0] == "gpg" and argv[1] == "--version":
            return subprocess.CompletedProcess(argv, 0, stdout="gpg (GnuPG) 2.4.9 (msys)\n", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    with pytest.raises(MsysGpgError) as exc_info:
        check_windows_msys_gpg(fake_run, tmp_path, platform="win32", env={})
    assert "PowerShell" in str(exc_info.value)
    assert "MSYS" in str(exc_info.value) or "msys" in str(exc_info.value).lower()


def test_passes_when_valid_gpg_configured(tmp_path: Path) -> None:
    def fake_run(argv, cwd):
        if list(argv[:3]) == ["git", "config", "--get"]:
            return subprocess.CompletedProcess(argv, 0, stdout=r"C:\Program Files\GnuPG\bin\gpg.exe" + "\n", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    # Should not raise
    check_windows_msys_gpg(fake_run, tmp_path, platform="win32", env={})


def test_skipped_on_linux(tmp_path: Path) -> None:
    def fake_run(argv, cwd):
        if list(argv[:3]) == ["git", "config", "--get"]:
            return subprocess.CompletedProcess(argv, 0, stdout="/usr/bin/gpg\n", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    # On non-win32 it should not raise even with /usr/bin/gpg
    check_windows_msys_gpg(fake_run, tmp_path, platform="linux", env={})


def test_shipper_raises_shipstop_on_msys_gpg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(argv, cwd):
        if list(argv[:3]) == ["git", "config", "--get"]:
            return subprocess.CompletedProcess(argv, 0, stdout="/usr/bin/gpg\n", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    # The check only fires on Windows; on any other host it would let the shipper go on to the branch check.
    monkeypatch.setattr(
        release_ship, "_gpg_check",
        lambda run, repo: check_windows_msys_gpg(run, repo, platform="win32", env={}),
    )
    shipper = Shipper(repo=tmp_path, engine_root=tmp_path, run=fake_run)
    with pytest.raises(ShipStop) as exc_info:
        shipper._prepare("patch", "")
    assert "PowerShell" in str(exc_info.value)
