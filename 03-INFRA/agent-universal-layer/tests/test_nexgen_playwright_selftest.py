"""The wrapper's patches still apply to the Playwright version it pins (opt-in: it downloads the package).

Run with NEXGEN_NETWORK_TESTS=1. The package is fetched into a private npm cache, so nothing of the
machine's own cache is read or patched. A version the wrapper cannot patch is refused rather than
half-patched, which is why raising the pin always starts here.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

WRAPPER = Path(__file__).resolve().parents[1] / "mcp" / "playwright-human-safe.mjs"

pytestmark = pytest.mark.skipif(
    os.environ.get("NEXGEN_NETWORK_TESTS") != "1" or not (shutil.which("node") and shutil.which("npm")),
    reason="downloads @playwright/mcp: set NEXGEN_NETWORK_TESTS=1 (needs node and npm)",
)


def test_every_patch_applies_to_the_pinned_version(tmp_path):
    env = {**os.environ, "npm_config_cache": str(tmp_path / "npm-cache")}
    result = subprocess.run(
        ["node", str(WRAPPER), "--self-test"], env=env, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300, check=False,
    )
    assert result.returncode == 0, result.stderr.strip() or result.stdout.strip()
