"""Which OpenCode config dialect the installed CLI accepts.

OpenCode 2 understands ordered `permissions` rules (`action`/`resource`/`effect`);
OpenCode 1 refuses the whole config with "V2 permissions are not supported by
OpenCode V1" and never starts. The engine targets V2, so on a machine that still
runs a V1 binary every write of that key bricked the CLI — and with it the
`acp-opencode` provider and the model catalog that depends on it.

One question, asked in one place: does this binary speak V2? Unknown counts as
V1: writing a key the CLI cannot parse is the failure that hurts, while omitting
a posture rule only leaves the guardrail to the plugin and the sidecar.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from functools import lru_cache

from .processes import probe_environment

_V2_PLURAL_KEY = "permissions"
_V1_SINGULAR_KEY = "permission"


@lru_cache(maxsize=4)
def _probe(binary: str) -> str | None:
    """The version this binary reports, or None when it cannot be read."""
    try:
        with probe_environment("nexgen-opencode-version-") as env:
            out = subprocess.run(
                [binary, "--version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                check=False,
                env=env,
            ).stdout
    except (OSError, subprocess.SubprocessError):  # nexgen: allow reason="a binary that cannot be started has no version to report, and the caller reads that as V1"
        return None
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", out or "")
    if not match:
        return None
    return match.group(0)


def binary_path() -> str | None:
    return shutil.which("opencode")


def installed_major() -> int | None:
    """The major of the `opencode` on PATH, or None when there is none to ask.

    None means "no readable answer", not "V1": a caller reporting a diagnosis
    needs the difference between a CLI that is too old and a CLI that is absent.
    """
    binary = binary_path()
    version = _probe(binary) if binary else None
    return int(version.split(".")[0]) if version else None


def installed_version() -> str | None:
    """The version string the `opencode` on PATH reports, for a diagnosis."""
    binary = binary_path()
    return _probe(binary) if binary else None


def permissions_key() -> str:
    """The key this machine's CLI reads: 'permissions' (V2) or 'permission' (V1)."""
    return _V2_PLURAL_KEY if (installed_major() or 0) >= 2 else _V1_SINGULAR_KEY


def supports_v2() -> bool:
    return permissions_key() == _V2_PLURAL_KEY
