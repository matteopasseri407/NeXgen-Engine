"""The Playwright wrapper's pinned package is visible to dependency watch and cannot drift."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import depwatch  # noqa: E402

MCP_DIR = Path(__file__).resolve().parents[1] / "mcp"


def test_the_manifest_declares_the_exact_version_the_wrapper_pins():
    wrapper = (MCP_DIR / "playwright-human-safe.mjs").read_text(encoding="utf-8")
    pinned = re.search(r"const VERSION = '([^']+)';", wrapper).group(1)
    manifest = yaml.safe_load((MCP_DIR / "manifest.yaml").read_text(encoding="utf-8"))
    assert manifest["servers"]["playwright"]["wraps"] == [f"@playwright/mcp@{pinned}"]


def test_dependency_watch_sees_a_package_a_launcher_pins_for_itself():
    pins = depwatch._collect_mcp_pins({
        "wrapped": {"command": "node", "args": ["wrapper.mjs"], "wraps": ["@scope/pkg@1.2.3"]},
        "plain": {"command": "node", "args": ["server.mjs"]},
        "npx": {"command": "npx", "args": ["-y", "other@4.5.6"]},
    })
    assert [(label, kind, pin, key) for label, kind, pin, key in pins] == [
        ("MCP server 'wrapped' (npm @scope/pkg)", "npm-version", "1.2.3", "@scope/pkg"),
        ("MCP server 'npx' (npm other)", "npm-version", "4.5.6", "other"),
    ]
