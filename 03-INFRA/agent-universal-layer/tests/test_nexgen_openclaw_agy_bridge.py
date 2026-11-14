"""The Antigravity plugin's MCP bridge, patched so agy receives its connectors once, lazily.

The `google-antigravity-cli` plugin bridges every connector mounted in OpenClaw
into agy's `mcp_config.json` with the `openclaw__` prefix. This causes agy to start
every connector directly, duplicating native ones and bypassing lazy-mcp on-demand loading.
Furthermore, if an agy run is killed before cleanup, leftover `openclaw__*` entries
remain in agy's file indefinitely.

The engine patches `dist/mcp-bridge.js` so that:
- ONLY the loopback server (`openclaw`) and the `lazy-mcp` gateway are injected;
- leftover `openclaw__*` entries are stripped at the START of a run before injecting;
- `native_connectors.loopback_deny` denies every mounted connector on the loopback when the patch is present;
- the doctor reports whether the bridge patch is in place.

One promise per test:
- an uninstalled plugin is reported as missing and writes nothing;
- the patch lands once, beside a backup, and cleans up;
- a second run finds the marker and writes nothing (idempotence);
- a file without the expected shape is left alone and reported as unexpected;
- a dry run prints the plan and writes nothing;
- setup helper reports one line per outcome;
- Node execution proves unpatched bridge injects all servers and keeps leftovers, while
  patched bridge injects only loopback and lazy-mcp and strips start leftovers;
- unprefixed keys from buildInjectedServers are handled cleanly;
- loopback_deny denies all mounted connectors when the bridge is patched, and preserves
  original behavior when not patched;
- doctor reports WARN when unpatched, OK when patched, and stays quiet when plugin is not installed.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import i18n  # noqa: E402
from nexgen_core.openclaw import agy_bridge, agy_prereqs, plugin_patch  # noqa: E402
from nexgen_core.openclaw.doctor_agy import BRIDGE_CHECK_ID, check_local_agy  # noqa: E402
from nexgen_core.openclaw.native_connectors import loopback_deny  # noqa: E402
from nexgen_core.openclaw.setup_agy import _bridge_line  # noqa: E402
from nexgen_core.report import Severity  # noqa: E402

NODE = shutil.which("node")
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "agy-plugin" / "mcp-bridge.js"


@pytest.fixture(autouse=True)
def _english(monkeypatch):
    """English output to assert on, whatever the machine's own language is."""
    i18n.set_language("en")


def _installed(tmp_path: Path, source: str | None = None) -> Path:
    """A state folder holding an installed plugin with the given compiled bridge."""
    path = agy_bridge.bridge_file(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = FIXTURE.read_text(encoding="utf-8") if source is None else source
    path.write_text(text, encoding="utf-8")
    return path


def test_a_plugin_that_is_not_installed_is_missing_and_writes_nothing(tmp_path: Path) -> None:
    result = agy_bridge.apply(tmp_path)

    assert result.state == plugin_patch.MISSING
    assert not agy_bridge.bridge_file(tmp_path).exists()


def test_the_patch_lands_once_and_the_old_file_is_saved(tmp_path: Path) -> None:
    path = _installed(tmp_path)
    before = path.read_text(encoding="utf-8")

    result = agy_bridge.apply(tmp_path)

    assert result.state == plugin_patch.PATCHED
    assert result.backup is not None and result.backup.exists()
    assert result.backup.read_text(encoding="utf-8") == before
    updated = path.read_text(encoding="utf-8")
    assert agy_bridge.MARKER in updated
    assert updated.count(agy_bridge.MARKER) == 1
    assert "stripOwnedServers(existing)" in updated
    assert "lazy-mcp" in updated
    assert "openclaw" in updated


def test_a_second_run_finds_its_marker_and_writes_nothing(tmp_path: Path) -> None:
    path = _installed(tmp_path)
    agy_bridge.apply(tmp_path)
    after_first = path.read_text(encoding="utf-8")

    result = agy_bridge.apply(tmp_path)

    assert result.state == plugin_patch.ALREADY
    assert result.backup is None
    assert path.read_text(encoding="utf-8") == after_first


def test_a_file_without_the_expected_shape_is_left_alone(tmp_path: Path) -> None:
    path = _installed(tmp_path, "export function applyOpenClawMcpBridge() { return null; }\n")
    before = path.read_text(encoding="utf-8")

    result = agy_bridge.apply(tmp_path)

    assert result.state == plugin_patch.UNEXPECTED
    assert path.read_text(encoding="utf-8") == before


def test_a_dry_run_plans_and_writes_nothing(tmp_path: Path) -> None:
    path = _installed(tmp_path)
    before = path.read_text(encoding="utf-8")

    result = agy_bridge.apply(tmp_path, dry_run=True)

    assert result.state == plugin_patch.WOULD_PATCH
    assert result.backup is None
    assert path.read_text(encoding="utf-8") == before


def test_the_setup_says_one_line_per_outcome(tmp_path: Path) -> None:
    path = _installed(tmp_path)

    assert "injects only the loopback server and lazy-mcp gateway" in (_bridge_line(agy_bridge.apply(tmp_path)) or "")
    assert _bridge_line(agy_bridge.apply(tmp_path)) is None
    unexpected = plugin_patch.Patch(plugin_patch.UNEXPECTED, path)
    assert "no longer has the shape" in (_bridge_line(unexpected) or "")
    failed = plugin_patch.Patch(plugin_patch.FAILED, path, detail="permission denied")
    assert "permission denied" in (_bridge_line(failed) or "")
    planned = plugin_patch.Patch(plugin_patch.WOULD_PATCH, path)
    assert "nothing is written" in (_bridge_line(planned) or "")


def _run_node_bridge(bridge_path: Path, settings: dict, existing: dict) -> dict:
    """Run applyOpenClawMcpBridge in node and return resulting agyConfig and cleanup result."""
    work_dir = bridge_path.parent / f"test-run-{bridge_path.stat().st_mtime_ns}"
    work_dir.mkdir(parents=True, exist_ok=True)
    settings_file = work_dir / "settings.json"
    settings_file.write_text(json.dumps(settings), encoding="utf-8")
    agy_config_file = work_dir / "mcp_config.json"
    agy_config_file.write_text(json.dumps(existing), encoding="utf-8")

    module = work_dir / "bridge.mjs"
    shutil.copyfile(bridge_path, module)

    script = f"""
    import path from "node:path";
    import fs from "node:fs/promises";
    import {{ applyOpenClawMcpBridge }} from {json.dumps(module.resolve().as_uri())};

    const res = await applyOpenClawMcpBridge({{
        settingsPath: {json.dumps(str(settings_file))},
        agyConfigPath: {json.dumps(str(agy_config_file))},
    }});
    const afterApply = JSON.parse(await fs.readFile({json.dumps(str(agy_config_file))}, "utf-8"));
    if (res?.cleanup) {{
        await res.cleanup();
    }}
    const afterCleanup = JSON.parse(await fs.readFile({json.dumps(str(agy_config_file))}, "utf-8"));
    process.stdout.write(JSON.stringify({{
        injectedCount: res?.injected,
        afterApply,
        afterCleanup,
    }}));
    """
    done = subprocess.run([NODE, "--input-type=module", "-e", script],
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_node_bridge_injects_only_openclaw_and_lazy_mcp_and_cleans_start_leftovers(tmp_path: Path) -> None:
    path = _installed(tmp_path)
    settings = {
        "mcpServers": {
            "openclaw": {"command": "openclaw"},
            "lazy-mcp": {"command": "lazy-mcp"},
            "drive": {"command": "drive"},
            "outlook": {"command": "outlook"},
        }
    }
    existing = {
        "mcpServers": {
            "vault-library": {"command": "node"},
            "openclaw__leftover_from_killed_run": {"command": "killed"},
        }
    }

    # 1. Unpatched bridge injects all servers and keeps leftover_from_killed_run
    unpatched_res = _run_node_bridge(path, settings, existing)
    unpatched_servers = unpatched_res["afterApply"]["mcpServers"]
    assert "openclaw__drive" in unpatched_servers
    assert "openclaw__outlook" in unpatched_servers
    assert "openclaw__leftover_from_killed_run" in unpatched_servers, "unpatched merge kept leftovers"

    # 2. Patch the bridge
    assert agy_bridge.apply(tmp_path).state == plugin_patch.PATCHED

    # 3. Patched bridge injects ONLY openclaw and lazy-mcp, and wipes leftover_from_killed_run at start
    patched_res = _run_node_bridge(path, settings, existing)
    patched_servers = patched_res["afterApply"]["mcpServers"]
    assert "vault-library" in patched_servers
    assert "openclaw__openclaw" in patched_servers
    assert "openclaw__lazy-mcp" in patched_servers
    assert "openclaw__drive" not in patched_servers, "drive must NOT be injected directly"
    assert "openclaw__outlook" not in patched_servers, "outlook must NOT be injected directly"
    assert "openclaw__leftover_from_killed_run" not in patched_servers, "leftovers must be removed at start"
    assert patched_res["injectedCount"] == 2

    # 4. Cleanup removes the injected servers
    cleaned_servers = patched_res["afterCleanup"]["mcpServers"]
    assert "vault-library" in cleaned_servers
    assert "openclaw__openclaw" not in cleaned_servers
    assert "openclaw__lazy-mcp" not in cleaned_servers


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_node_bridge_handles_unprefixed_keys(tmp_path: Path) -> None:
    # Fixture where buildInjectedServers returns keys without prefix
    source = FIXTURE.read_text(encoding="utf-8").replace(
        "k.startsWith(OPENCLAW_MCP_SERVER_PREFIX) ? k : `${OPENCLAW_MCP_SERVER_PREFIX}${k}`",
        "k",
    )
    path = _installed(tmp_path, source)
    assert agy_bridge.apply(tmp_path).state == plugin_patch.PATCHED

    settings = {
        "mcpServers": {
            "openclaw": {"command": "openclaw"},
            "lazy-mcp": {"command": "lazy-mcp"},
            "drive": {"command": "drive"},
        }
    }
    existing = {"mcpServers": {"vault-library": {"command": "node"}}}
    res = _run_node_bridge(path, settings, existing)
    servers = res["afterApply"]["mcpServers"]
    assert "openclaw" in servers
    assert "lazy-mcp" in servers
    assert "drive" not in servers


def test_loopback_deny_denies_all_mounted_when_bridge_is_patched(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    state = home / ".openclaw"
    state.mkdir()

    config = {
        "agents": {
            "entries": {
                "gemini": {
                    "model": "google-antigravity-cli/gemini-3.8-flash",
                }
            }
        }
    }
    connectors = ["vault-library", "n8n-mcp", "drive", "outlook"]

    # 1. Unpatched: agy file has only vault-library -> only vault-library denied
    agy_cfg = home / ".gemini" / "antigravity-cli" / "mcp_config.json"
    agy_cfg.parent.mkdir(parents=True)
    agy_cfg.write_text(json.dumps({"mcpServers": {"vault-library": {}}}), encoding="utf-8")

    unpatched_deny = loopback_deny(config, connectors, home)
    assert unpatched_deny.agy == ["gemini"]
    assert unpatched_deny.agy_connectors == ["vault-library"]

    # 2. Patch the bridge
    _installed(state)
    assert agy_bridge.apply(state).state == plugin_patch.PATCHED
    assert agy_bridge.patched(state) is True

    # 3. Patched: denies EVERY mounted connector
    patched_deny = loopback_deny(config, connectors, home)
    assert patched_deny.agy == ["gemini"]
    assert set(patched_deny.agy_connectors) == set(connectors)


def test_doctor_reports_bridge_status(tmp_path: Path) -> None:
    state = tmp_path / "state"
    home = tmp_path / "home"
    backend_dir = agy_prereqs.extension_dir(state)
    backend_dir.mkdir(parents=True)
    (backend_dir / "package.json").write_text(json.dumps({"version": "0.3.4"}), encoding="utf-8")

    config = {
        "plugins": {
            "allow": ["google-antigravity-cli"],
            "entries": {"google-antigravity-cli": {"enabled": True}},
        },
        "agents": {
            "entries": {
                "gemini": {"model": "google-antigravity-cli/gemini-3.8-flash"}
            }
        }
    }

    # 1. Unpatched -> WARN
    outcomes = {o.id: o for o in check_local_agy(config, state_dir=state, home=home, which=lambda name: "/fake/bin/agy")}
    assert BRIDGE_CHECK_ID in outcomes
    assert outcomes[BRIDGE_CHECK_ID].severity == Severity.WARN
    assert "not patched" in outcomes[BRIDGE_CHECK_ID].message
    assert "nexgen openclaw setup" in (outcomes[BRIDGE_CHECK_ID].action or "")

    # 2. Patched -> OK
    _installed(state)
    assert agy_bridge.apply(state).state == plugin_patch.PATCHED
    outcomes = {o.id: o for o in check_local_agy(config, state_dir=state, home=home, which=lambda name: "/fake/bin/agy")}
    assert BRIDGE_CHECK_ID in outcomes
    assert outcomes[BRIDGE_CHECK_ID].severity == Severity.OK
    assert "is patched" in outcomes[BRIDGE_CHECK_ID].message

    # 3. Not installed -> no bridge check
    uninstalled_state = tmp_path / "uninstalled"
    uninstalled_state.mkdir()
    outcomes = {o.id: o for o in check_local_agy(config, state_dir=uninstalled_state, home=home, which=lambda name: "/fake/bin/agy")}
    assert BRIDGE_CHECK_ID not in outcomes
