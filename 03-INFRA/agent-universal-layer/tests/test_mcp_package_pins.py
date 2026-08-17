"""Regression tests for local MCP package supply-chain pins."""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


REPO = Path(__file__).resolve().parents[3]
MANIFEST = REPO / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
RENDER = REPO / "03-INFRA" / "agent-universal-layer" / "mcp" / "render.py"
PLAYWRIGHT_WRAPPER = REPO / "03-INFRA" / "agent-universal-layer" / "mcp" / "playwright-human-safe.mjs"
HTTP_BRIDGE = REPO / "03-INFRA" / "agent-universal-layer" / "mcp" / "mcp-http-bridge.mjs"
EXACT_NPM_PIN = re.compile(r"^(?:@[-a-z0-9_.]+/)?[-a-z0-9_.]+@\d+(?:\.\d+){2}$", re.I)
NPM_COLD_START_TIMEOUT = 120


def _is_exact_npm_pin(package: str) -> bool:
    return bool(EXACT_NPM_PIN.fullmatch(package))


def test_manifest_pins_every_npx_package():
    servers = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))["servers"]

    for name, server in servers.items():
        if server.get("command") != "npx":
            continue
        package = next(arg for arg in server["args"] if not arg.startswith("-"))
        assert _is_exact_npm_pin(package), f"{name}: npx package must use an exact version, got {package!r}"


def test_antigravity_http_bridge_is_pinned():
    tree = ast.parse(RENDER.read_text(encoding="utf-8"))
    package = next(
        node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id == "MCP_REMOTE_PACKAGE"
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
    )

    assert _is_exact_npm_pin(package), f"mcp-remote must use an exact version, got {package!r}"
    bridge = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "r_antigravity"
    )
    assert any(
        isinstance(node, ast.Name) and node.id == "MCP_REMOTE_PACKAGE"
        for node in ast.walk(bridge)
    ), "r_antigravity must render the pinned mcp-remote package"


def test_antigravity_http_bridge_keeps_the_token_out_of_child_arguments(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed on this test host")
    output = tmp_path / "spawn.json"
    fake_npm = tmp_path / "fake-npm.mjs"
    fake_npm.write_text(
        "import fs from 'node:fs';\n"
        "fs.writeFileSync(process.env.NEXGEN_TEST_OUTPUT, JSON.stringify({"
        "argv: process.argv.slice(2), header: process.env.NEXGEN_MCP_AUTH_HEADER}));\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.update({
        "npm_execpath": str(fake_npm),
        "nexgen_test_token": "fixture-secret-value",
        "NEXGEN_TEST_OUTPUT": str(output),
    })

    result = subprocess.run(
        [
            node, str(HTTP_BRIDGE), "https://example.invalid/mcp",
            "nexgen_test_token", "mcp-remote@0.1.38",
        ],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    launched = json.loads(output.read_text(encoding="utf-8"))
    assert launched["header"] == "Bearer fixture-secret-value"
    assert "fixture-secret-value" not in " ".join(launched["argv"])
    assert "Authorization:${NEXGEN_MCP_AUTH_HEADER}" in launched["argv"]


def test_http_bridge_survives_a_signalled_child_without_crashing(tmp_path):
    """A child killed by a signal must not make the wrapper throw; it re-signals itself instead."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed on this test host")
    fake_npm = tmp_path / "fake-npm.js"
    fake_npm.write_text("process.kill(process.pid, 'SIGTERM');\n", encoding="utf-8")
    env = os.environ.copy()
    env.update({
        "npm_execpath": str(fake_npm),
        "PROBE_TOKEN": "probe-value",
    })

    result = subprocess.run(
        [
            node, str(HTTP_BRIDGE), "http://127.0.0.1:9/mcp",
            "PROBE_TOKEN", "mcp-remote@0.1.38",
        ],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )

    assert "TypeError" not in result.stderr
    if os.name == "nt":
        # Windows has no POSIX signals: Node terminates the child and reports an
        # ordinary exit code, so the wrapper propagates that code instead of
        # re-signalling itself. What must still hold on both platforms is that a
        # child's violent death is never reported as success.
        assert result.returncode != 0, result.stdout + result.stderr
    else:
        assert result.returncode in (-15, 143), result.stdout + result.stderr


def test_http_bridge_adds_the_headers_the_revision_requires(tmp_path):
    """MCP revision 2026-07-28 requires Mcp-Method on every Streamable HTTP POST
    and Mcp-Name whenever the body names a target. mcp-remote predates it and
    0.1.38 is its last published version, so a server that enforces the revision
    (n8n does) answers 400 to every request and the client waits forever for a
    tool list -- which reads as a hang rather than the protocol error it is.
    The bridge derives the headers from the body it is already forwarding.

    Importing the module here also pins the entry-point guard: a bridge that ran
    main() on import would try to spawn mcp-remote during the test run.
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed on this test host")
    payloads = [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "search_workflows",
                    "_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28"}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    script = tmp_path / "derive.mjs"
    script.write_text(
        "import { deriveRevisionHeaders } from %s;\n"
        "const bodies = %s;\n"
        "const out = bodies.map((body) => deriveRevisionHeaders(body));\n"
        "out.push(deriveRevisionHeaders('not json at all'));\n"
        "process.stdout.write(JSON.stringify(out));\n"
        % (json.dumps(HTTP_BRIDGE.as_uri()), json.dumps([json.dumps(p) for p in payloads])),
        encoding="utf-8",
    )

    result = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=30)

    assert result.returncode == 0, result.stdout + result.stderr
    call, listing, junk = json.loads(result.stdout)
    assert call["mcp-method"] == "tools/call"
    assert call["mcp-name"] == "search_workflows"
    assert call["mcp-protocol-version"] == "2026-07-28"
    # No name in the body means no Mcp-Name: headers and body must never disagree.
    assert listing == {"mcp-method": "tools/list"}
    # A body we cannot read is one we must not describe.
    assert junk == {}


def test_http_bridge_runs_when_its_path_is_not_spelled_identically(tmp_path):
    """The bridge only runs main() when launched as a program, so that a test
    can import it without spawning anything. That guard must compare what the
    paths resolve to, not their spelling: on Windows the filesystem is
    case-insensitive while string comparison is not, and the launcher's path
    need not match the module's letter for letter. A guard that got this wrong
    would fail silently -- the bridge exits without starting and the MCP server
    looks dead for reasons nothing reports. A symlink reproduces the same
    mismatch portably."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed on this test host")
    link = tmp_path / "bridge-via-another-name.mjs"
    try:
        link.symlink_to(HTTP_BRIDGE)
    except (OSError, NotImplementedError):
        pytest.skip("this host cannot create symlinks without extra privileges")

    result = subprocess.run(
        [node, str(link), "--self-test", "mcp-remote@0.1.38"],
        capture_output=True, text=True, timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_http_bridge_shim_listens_only_on_loopback():
    """The shim carries the bearer mcp-remote sets. Bound to anything but
    loopback it would offer an authenticated path to the MCP server to the
    whole network."""
    bridge = HTTP_BRIDGE.read_text(encoding="utf-8")
    assert "server.listen(0, '127.0.0.1'" in bridge


def test_playwright_wrapper_and_manifest_share_an_exact_pin():
    wrapper = PLAYWRIGHT_WRAPPER.read_text(encoding="utf-8")
    match = re.search(r"const VERSION = '([^']+)';", wrapper)
    assert match, "Playwright wrapper must declare its reviewed upstream version"
    assert _is_exact_npm_pin(f"@playwright/mcp@{match.group(1)}")

    server = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))["servers"]["playwright"]
    assert server["command"] == "node"
    assert any("playwright-human-safe.mjs" in arg for arg in server["args"])
    assert any("playwright-human-safe.mjs" in arg for arg in server["windows"]["args"])


def test_playwright_wrapper_preserves_shared_chrome_downloads():
    wrapper = PLAYWRIGHT_WRAPPER.read_text(encoding="utf-8")

    assert "const DOWNLOAD_MARKER = 'agent-preserve-shared-downloads-patch-v1';" in wrapper
    assert "Browser.setDownloadBehavior" in wrapper
    assert "occurrences(fileChooserPatched, upstreamDownloadBehavior) !== 1" in wrapper
    assert ".replace(upstreamDownloadBehavior, nativeDownloadBehavior)" in wrapper


def test_playwright_wrapper_focuses_newly_created_tabs():
    wrapper = PLAYWRIGHT_WRAPPER.read_text(encoding="utf-8")

    assert "const NEW_TAB_FOCUS_MARKER = 'agent-focus-new-tab-patch-v1';" in wrapper
    assert "await page.bringToFront();" in wrapper
    assert "occurrences(cdpDisposalPatched, upstreamNewTab) !== 1" in wrapper
    assert ".replace(upstreamNewTab, focusedNewTab)" in wrapper


def test_playwright_wrapper_detaches_from_a_shared_cdp_context():
    wrapper = PLAYWRIGHT_WRAPPER.read_text(encoding="utf-8")

    assert "const CDP_DISPOSAL_MARKER = 'agent-preserve-shared-cdp-context-patch-v1';" in wrapper
    assert "config.browser.cdpEndpoint" in wrapper
    assert "function patchCdpDisposal(source, bundle)" in wrapper
    assert "Refusing an unsafe partial patch" in wrapper


def test_playwright_wrapper_can_resolve_npm_without_spawning_a_cmd_shim():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed on this test host")
    result = subprocess.run(
        [node, str(PLAYWRIGHT_WRAPPER), "--self-test"],
        capture_output=True,
        text=True,
        # A GitHub-hosted Windows runner starts with an empty npm cache. This
        # self-test deliberately prepares the exact reviewed package before
        # validating its bundle, so it needs the same cold-network budget as
        # the explicit overlong-PATH regression below.
        timeout=NPM_COLD_START_TIMEOUT,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(os.name != "nt", reason="The cmd.exe PATH ceiling is Windows-specific.")
def test_playwright_wrapper_cold_cache_survives_an_overlong_windows_path(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed on this test host")
    env = os.environ.copy()
    path_key = next((key for key in env if key.lower() == "path"), "PATH")
    env[path_key] = ("X" * 9000) + os.pathsep + env.get(path_key, "")
    env["npm_config_cache"] = str(tmp_path / "npm-cache")

    result = subprocess.run(
        [node, str(PLAYWRIGHT_WRAPPER), "--self-test"],
        capture_output=True,
        text=True,
        timeout=NPM_COLD_START_TIMEOUT,
        env=env,
    )

    assert result.returncode == 0, result.stdout + result.stderr
