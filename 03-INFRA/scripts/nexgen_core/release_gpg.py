"""GPG checks for release signing on Windows.

One job: ensure git signing does not invoke MSYS gpg from Git Bash, which
fails on Windows without a usable pinentry or Windows keystore access.
"""
from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path


class MsysGpgError(RuntimeError):
    """Raised when MSYS gpg is detected during a Windows release run."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.step = "prepare"
        self.reason = message


def is_msys_gpg(path_or_output: str) -> bool:
    norm = path_or_output.replace("\\", "/").lower()
    return "usr/bin/gpg" in norm or "msys" in norm or norm.startswith("/usr/bin") or "/git/usr/bin" in norm


def check_windows_msys_gpg(
    run: Callable[[Sequence[str], Path], subprocess.CompletedProcess[str]],
    repo: Path,
    platform: str | None = None,
    env: dict[str, str] | None = None,
    stop_factory: Callable[[str, str], Exception] | None = None,
) -> None:
    """Windows release ship fails from Git Bash (MSYS gpg): detect and stop early."""
    plat = platform if platform is not None else sys.platform
    if plat != "win32":
        return
    maker = stop_factory or (lambda step, reason: MsysGpgError(reason))
    res = run(["git", "config", "--get", "gpg.program"], repo)
    prog = (res.stdout or "").strip()
    if prog:
        if is_msys_gpg(prog):
            raise maker(
                "prepare",
                "Release ship su Windows fallisce con il gpg di MSYS (Git Bash). "
                "Usa PowerShell per il release ship.",
            )
        return
    gpg_res = run(["gpg", "--version"], repo)
    out = (gpg_res.stdout or "").strip()
    if out and is_msys_gpg(out):
        raise maker(
            "prepare",
            "Release ship su Windows fallisce con il gpg di MSYS (Git Bash). "
            "Usa PowerShell per il release ship.",
        )
