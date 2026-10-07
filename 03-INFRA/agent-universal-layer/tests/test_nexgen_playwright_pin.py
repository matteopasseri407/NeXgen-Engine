"""The Playwright wrapper's pinned package is declared once, by its module, and cannot drift."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import depwatch  # noqa: E402
from nexgen_core.modules import load_catalog  # noqa: E402

ENGINE = Path(__file__).resolve().parents[2]
MCP_DIR = Path(__file__).resolve().parents[1] / "mcp"


def _browser_upstream():
    (component,) = load_catalog(ENGINE)["browser"].upstream
    return component


def test_the_browser_module_reads_the_version_the_wrapper_really_pins():
    wrapper = (MCP_DIR / "playwright-human-safe.mjs").read_text(encoding="utf-8")
    pinned = re.search(r"const VERSION = '([^']+)';", wrapper).group(1)
    assert depwatch._module_pin_version(ENGINE, _browser_upstream()) == pinned


def test_the_module_asks_npm_about_the_package_the_wrapper_runs():
    component = _browser_upstream()
    assert (component.kind, component.package, component.latest) == ("npm", "@playwright/mcp", "npm:@playwright/mcp")


def test_the_mcp_manifest_no_longer_carries_a_second_copy_of_the_pin():
    """A number in the manifest could be rewritten by the bump without ever changing the wrapper's."""
    manifest = yaml.safe_load((MCP_DIR / "manifest.yaml").read_text(encoding="utf-8"))
    assert "wraps" not in manifest["servers"]["playwright"]


def test_dependency_watch_ignores_a_stale_wraps_copy_in_a_manifest_that_still_has_one():
    pins = depwatch._collect_mcp_pins({
        "wrapped": {"command": "node", "args": ["wrapper.mjs"], "wraps": ["@scope/pkg@1.2.3"]},
        "plain": {"command": "node", "args": ["server.mjs"]},
        "npx": {"command": "npx", "args": ["-y", "other@4.5.6"]},
    })
    assert [(label, kind, pin, key) for label, kind, pin, key in pins] == [
        ("MCP server 'npx' (npm other)", "npm-version", "4.5.6", "other"),
    ]
