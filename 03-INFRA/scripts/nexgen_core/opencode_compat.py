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

_V2_PLURAL_KEY = "permissions"
_V1_SINGULAR_KEY = "permission"


@lru_cache(maxsize=4)
def _major_of(binary: str) -> int | None:
    try:
        out = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", out or "")
    if not match:
        return None
    return int(match.group(1))


def binary_path() -> str | None:
    return shutil.which("opencode")


def permissions_key() -> str:
    """The key this machine's CLI reads: 'permissions' (V2) or 'permission' (V1)."""
    binary = binary_path()
    major = _major_of(binary) if binary else None
    return _V2_PLURAL_KEY if (major or 0) >= 2 else _V1_SINGULAR_KEY


def supports_v2() -> bool:
    return permissions_key() == _V2_PLURAL_KEY
