"""OpenClaw's guardrail: the plugin is deposited, enabled by setup, and it blocks.

OpenClaw's hook is a JS callback inside its gateway process, so the engine ships a
native plugin. These tests run the real files: the deposit (byte for byte, beside
the state directory, never touching `openclaw.json`) and the plugin itself under
node, with a body that allows, one that denies and one that asks.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import REAL_UL
from nexgen_core.checks.guardrail_checks import check_openclaw_guardrail_plugin
from nexgen_core.paths import openclaw_config
from nexgen_core.report import Severity
from nexgen_core.runtimes import apply_all
from nexgen_core.runtimes.openclaw import PLUGIN_ID, OpenClawRuntime

HOOKS = REAL_UL / "hooks"
PLUGIN_SOURCE = HOOKS / "openclaw-guardrail-plugin"
NODE = shutil.which("node")

OPENCLAW_JSON = {
    "gateway": {"port": 18789},
    "agents": {"defaults": {"workspace": "~/work"}},
    "mcp": {"servers": {"vault-library": {"command": "npx"}}},
}


def a_home(tmp_path: Path, config: dict | None = None) -> Path:
    home = tmp_path / "home"
    state = home / ".openclaw"
    state.mkdir(parents=True)
    if config is not None:
        (state / "openclaw.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    return home


def a_body(tmp_path: Path) -> Path:
    body = tmp_path / "guardrail-body.mjs"
    body.write_text("// the private policy, in the test only\n", encoding="utf-8")
    return body


def plugin_dir(home: Path) -> Path:
    return home / ".openclaw" / "extensions" / PLUGIN_ID


# ------------------------------------------------------------------ the deposit


def test_the_plugin_is_deposited_byte_for_byte_beside_the_state_directory(tmp_path):
    home = a_home(tmp_path, OPENCLAW_JSON)
    body = a_body(tmp_path)

    action = OpenClawRuntime().install_guardrail(home, body, HOOKS)

    deposited = plugin_dir(home)
    assert action and str(deposited) in action
    for name in ("index.mjs", "package.json", "openclaw.plugin.json"):
        assert (deposited / name).read_bytes() == (PLUGIN_SOURCE / name).read_bytes(), name
    assert (deposited / "nexgen-guardrail-core.mjs").read_bytes() == (
        HOOKS / "nexgen-guardrail-core.mjs"
    ).read_bytes()
    assert (deposited / "hooks" / body.name).read_bytes() == body.read_bytes()


def test_the_sidecar_points_at_the_deposited_body_and_records_the_audit_file(tmp_path):
    home = a_home(tmp_path, OPENCLAW_JSON)
    body = a_body(tmp_path)

    OpenClawRuntime().install_guardrail(home, body, HOOKS)

    sidecar = json.loads((plugin_dir(home) / "nexgen-guardrail.config.json").read_text(encoding="utf-8"))
    assert sidecar["hooks"][0]["file"] == str(plugin_dir(home) / "hooks" / body.name)
    audit = Path(sidecar["auditFile"])
    assert audit.name == "openclaw.json" and audit.parent.name == "guardrail"


def test_the_second_run_deposits_nothing_again(tmp_path):
    home = a_home(tmp_path, OPENCLAW_JSON)
    body = a_body(tmp_path)
    runtime = OpenClawRuntime()

    runtime.install_guardrail(home, body, HOOKS)
    assert runtime.install_guardrail(home, body, HOOKS) is None


def test_the_permissions_phase_never_touches_openclaw_json(tmp_path):
    home = a_home(tmp_path, OPENCLAW_JSON)
    body = a_body(tmp_path)
    config_path = openclaw_config(home)
    before = config_path.read_bytes()

    apply_all(home=home, engine_hooks_dir=HOOKS, posture={}, guardrail_source=body)

    assert config_path.read_bytes() == before
    assert plugin_dir(home).is_dir()


def test_without_a_config_there_is_no_state_directory_to_deposit_into(tmp_path):
    home = a_home(tmp_path, None)
    body = a_body(tmp_path)

    assert OpenClawRuntime().install_guardrail(home, body, HOOKS) is None
    assert not (home / ".openclaw" / "extensions").exists()


def test_a_missing_engine_plugin_is_an_error_not_a_silent_skip(tmp_path):
    from nexgen_core.runtimes.base import GuardrailError

    home = a_home(tmp_path, OPENCLAW_JSON)
    with pytest.raises(GuardrailError):
        OpenClawRuntime().install_guardrail(home, a_body(tmp_path), tmp_path / "no-hooks-here")


# --------------------------------------------------------------- the doctor


def test_the_doctor_says_the_plugin_is_not_deposited_yet(tmp_path):
    home = a_home(tmp_path, OPENCLAW_JSON)

    outcome = check_openclaw_guardrail_plugin(home)

    assert outcome is not None and outcome.severity is Severity.WARN
    assert "nexgen sync" in (outcome.action or "")


def test_the_doctor_is_green_once_the_plugin_is_deposited_without_an_allowlist(tmp_path):
    home = a_home(tmp_path, OPENCLAW_JSON)
    OpenClawRuntime().install_guardrail(home, a_body(tmp_path), HOOKS)

    outcome = check_openclaw_guardrail_plugin(home)

    assert outcome is not None and outcome.severity is Severity.OK


def test_an_allowlist_that_names_other_plugins_only_is_not_read_as_enabled(tmp_path):
    config = {**OPENCLAW_JSON, "plugins": {"allow": ["someone-else"]}}
    home = a_home(tmp_path, config)
    OpenClawRuntime().install_guardrail(home, a_body(tmp_path), HOOKS)

    outcome = check_openclaw_guardrail_plugin(home)

    assert outcome is not None and outcome.severity is Severity.WARN
    assert "nexgen openclaw setup" in (outcome.action or "")


def test_an_allowlist_that_names_the_plugin_is_enabled(tmp_path):
    config = {**OPENCLAW_JSON, "plugins": {"allow": [PLUGIN_ID]}}
    home = a_home(tmp_path, config)
    OpenClawRuntime().install_guardrail(home, a_body(tmp_path), HOOKS)

    assert check_openclaw_guardrail_plugin(home).severity is Severity.OK


def test_a_deny_entry_is_never_read_as_enabled(tmp_path):
    config = {**OPENCLAW_JSON, "plugins": {"allow": [PLUGIN_ID], "deny": [PLUGIN_ID]}}
    home = a_home(tmp_path, config)
    OpenClawRuntime().install_guardrail(home, a_body(tmp_path), HOOKS)

    assert check_openclaw_guardrail_plugin(home).severity is Severity.WARN


def test_a_disabled_entry_is_not_read_as_enabled(tmp_path):
    config = {**OPENCLAW_JSON, "plugins": {"entries": {PLUGIN_ID: {"enabled": False}}}}
    home = a_home(tmp_path, config)
    OpenClawRuntime().install_guardrail(home, a_body(tmp_path), HOOKS)

    assert check_openclaw_guardrail_plugin(home).severity is Severity.WARN


def test_the_doctor_says_nothing_about_a_cli_that_is_not_installed(tmp_path, monkeypatch):
    home = a_home(tmp_path, OPENCLAW_JSON)
    monkeypatch.setattr(OpenClawRuntime, "is_installed", lambda self, home: False)

    assert check_openclaw_guardrail_plugin(home) is None


# ----------------------------------------------------------- the shipped files


def test_the_shipped_manifest_declares_a_startup_hook_plugin():
    package = json.loads((PLUGIN_SOURCE / "package.json").read_text(encoding="utf-8"))
    manifest = json.loads((PLUGIN_SOURCE / "openclaw.plugin.json").read_text(encoding="utf-8"))
    assert package["type"] == "module"
    assert package["openclaw"]["extensions"] == ["./index.mjs"]
    assert manifest["id"] == PLUGIN_ID
    assert manifest["activation"]["onStartup"] is True
    assert manifest["configSchema"] == {"type": "object", "additionalProperties": False}


# ------------------------------------------------------------- the plugin, run
#
# The hook contract these tests fire is the one OpenClaw 2026.10.6 documents: a
# handler registered with `api.on("before_tool_call", fn)` that returns
# `{ block, blockReason }`, `{ requireApproval }` or nothing, and receives
# `(event, ctx)` with `event.toolName` / `event.params`.

BODIES = {
    "allow.mjs": "process.stdin.resume(); process.stdin.on('end', () => process.exit(0));",
    "deny.mjs": (
        "process.stdin.resume(); process.stdin.on('end', () => { process.stdout.write(JSON.stringify("
        "{hookSpecificOutput:{permissionDecision:'deny',permissionDecisionReason:'rm -rf is not allowed'}})); });"
    ),
    "ask.mjs": (
        "process.stdin.resume(); process.stdin.on('end', () => { process.stdout.write(JSON.stringify("
        "{hookSpecificOutput:{permissionDecision:'ask',permissionDecisionReason:'looks risky'}})); });"
    ),
    "crash.mjs": "process.exit(1);",
}


def deploy(tmp_path: Path, body: str, **sidecar) -> Path:
    """The plugin, the core and the sidecar side by side, as the guard deploys them."""
    target = tmp_path / "deployed"
    target.mkdir()
    # Every file of the plugin, not a list of names: the guard copies the folder, and the
    # plugin now holds four jobs (guardrail, briefing, quota, workshop) beside its index.
    for source in sorted(PLUGIN_SOURCE.iterdir()):
        if source.is_file():
            shutil.copy2(source, target / source.name)
    shutil.copy2(HOOKS / "nexgen-guardrail-core.mjs", target / "nexgen-guardrail-core.mjs")
    (target / "body.mjs").write_text(BODIES[body], encoding="utf-8")
    config = {"hooks": [{"file": str(target / "body.mjs"), "timeout": sidecar.pop("timeout", 5)}], **sidecar}
    (target / "nexgen-guardrail.config.json").write_text(json.dumps(config), encoding="utf-8")
    return target


def fire(target: Path, tool_name: str = "exec", params: dict | None = None, ctx: dict | None = None) -> dict:
    """Loads the plugin the way OpenClaw does, fires one tool call, returns what the hook answered."""
    driver = target / "driver.mjs"
    driver.write_text(
        f"import plugin from {json.dumps((target / 'index.mjs').as_uri())};\n"
        "const handlers = [];\n"
        "plugin.register({ on: (name, fn) => { handlers.push([name, fn]); } });\n"
        "const [name, handler] = handlers[0];\n"
        f"const event = {json.dumps({'toolName': tool_name, 'params': params if params is not None else {'command': 'ls'}})};\n"
        f"const decision = await handler(event, {json.dumps(ctx or {'sessionId': 's1'})});\n"
        "console.log(JSON.stringify({ id: plugin.id, name, decision: decision ?? null }));\n",
        encoding="utf-8",
    )
    done = subprocess.run([NODE, str(driver)], capture_output=True, text=True, timeout=60, check=False)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_plugin_registers_one_decision_hook(tmp_path):
    fired = fire(deploy(tmp_path, "allow.mjs"))
    assert fired == {"id": PLUGIN_ID, "name": "before_tool_call", "decision": None}


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_denied_command_is_blocked_with_the_bodys_reason(tmp_path):
    decision = fire(deploy(tmp_path, "deny.mjs"))["decision"]
    assert decision["block"] is True
    assert "rm -rf is not allowed" in decision["blockReason"]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_an_allowed_command_is_left_alone(tmp_path):
    assert fire(deploy(tmp_path, "allow.mjs"))["decision"] is None


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_command_the_body_questions_asks_the_person(tmp_path):
    decision = fire(deploy(tmp_path, "ask.mjs"))["decision"]
    assert "requireApproval" in decision
    assert "looks risky" in decision["requireApproval"]["description"]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_tool_that_runs_no_command_is_not_judged(tmp_path):
    target = deploy(tmp_path, "deny.mjs")
    assert fire(target, tool_name="read", params={"path": "/tmp/x"})["decision"] is None
    assert fire(target, tool_name="exec", params={"workdir": "/tmp"})["decision"] is None


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_missing_core_blocks_instead_of_allowing(tmp_path):
    target = deploy(tmp_path, "allow.mjs")
    (target / "nexgen-guardrail-core.mjs").unlink()

    decision = fire(target)["decision"]

    assert decision["block"] is True
    assert "core" in decision["blockReason"]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_corrupt_sidecar_is_not_read_as_no_guardrail(tmp_path):
    target = deploy(tmp_path, "allow.mjs")
    (target / "nexgen-guardrail.config.json").write_text("{not json", encoding="utf-8")

    decision = fire(target)["decision"]

    assert "requireApproval" in decision  # a person is asked; it is never waved through


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_broken_body_blocks_when_the_sidecar_is_strict(tmp_path):
    decision = fire(deploy(tmp_path, "crash.mjs", strict=True))["decision"]
    assert decision["block"] is True


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_broken_body_asks_a_person_when_it_is_not_strict(tmp_path):
    decision = fire(deploy(tmp_path, "crash.mjs"))["decision"]
    assert "requireApproval" in decision


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_every_command_is_recorded_as_a_consultation(tmp_path):
    audit = tmp_path / "state" / "openclaw.json"
    target = deploy(tmp_path, "allow.mjs", auditFile=str(audit))

    fire(target)
    fire(target)

    record = json.loads(audit.read_text(encoding="utf-8"))
    assert record["cli"] == "openclaw" and record["count"] == 2 and record["last"] == "allow"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_denied_command_is_recorded_as_a_denial(tmp_path):
    audit = tmp_path / "state" / "openclaw.json"
    fire(deploy(tmp_path, "deny.mjs", auditFile=str(audit)))
    assert json.loads(audit.read_text(encoding="utf-8"))["last"] == "deny"
