"""The guardrail adapters, executed: what a CLI actually receives when the guardrail works and when it does not.

The three adapters (one per CLI) had no test that ran them. Claude's had no adapter at all: its
hook was registered directly, and Claude treats any exit code other than 2, and any timeout, as a
non-blocking error. A body that crashed, was missing or hung let every command through while the
registration looked healthy, in the one posture (bypassPermissions) where the guardrail is the only
brake. These tests run the real files with node.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from conftest import REAL_VAULT

HOOKS = REAL_VAULT / "03-INFRA" / "agent-universal-layer" / "hooks"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

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
    "hang.mjs": "setTimeout(() => {}, 60000);",
    "garbage.mjs": "process.stdin.resume(); process.stdin.on('end', () => process.stdout.write('not json at all'));",
    "undecided.mjs": "process.stdin.resume(); process.stdin.on('end', () => process.stdout.write('{\"hello\":1}'));",
    # Records what the body was given, so the adapters' translation can be asserted.
    "echo.mjs": (
        "const fs = require('fs');"
    ),
}
BODIES["echo.mjs"] = (
    "import { readFileSync, writeFileSync } from 'node:fs';"
    "writeFileSync(process.env.GUARDRAIL_ECHO, readFileSync(0, 'utf8'));"
)


def deploy(tmp_path: Path, adapter: str, body: str | None, **sidecar) -> Path:
    """The adapter, the core and the sidecar side by side, as the guard deploys them."""
    target = tmp_path / "deployed"
    target.mkdir()
    for name in (adapter, "nexgen-guardrail-core.mjs"):
        shutil.copy2(HOOKS / name, target / name)
    if body is not None:
        (target / body).write_text(BODIES[body], encoding="utf-8")
    hooks = [] if body is None else [{"file": str(target / body), "timeout": sidecar.pop("timeout", 5)}]
    config = {"hooks": hooks, **sidecar}
    (target / "nexgen-guardrail.config.json").write_text(json.dumps(config), encoding="utf-8")
    return target / adapter


def run(adapter: Path, stdin: str, **env) -> subprocess.CompletedProcess[str]:
    import os

    return subprocess.run([NODE, str(adapter)], input=stdin, capture_output=True, text=True, timeout=60,
                          env={**os.environ, **env}, check=False)


CLAUDE_INPUT = json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash",
                           "tool_input": {"command": "ls"}, "permission_mode": "default"})
BYPASS_INPUT = json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash",
                           "tool_input": {"command": "ls"}, "permission_mode": "bypassPermissions"})


def claude_decision(result: subprocess.CompletedProcess[str]) -> str:
    """What Claude Code would do with this hook's output."""
    if result.returncode == 2:
        return "deny"  # exit 2 is a blocking error
    assert result.returncode == 0, f"exit {result.returncode} would let the command run: {result.stderr}"
    if not result.stdout.strip():
        return "allow"
    return json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"]


# ------------------------------------------------------------------ Claude


@pytest.mark.parametrize(("body", "expected"), [("allow.mjs", "allow"), ("deny.mjs", "deny"), ("ask.mjs", "ask")])
def test_claude_adapter_passes_the_bodys_decision_through(tmp_path, body, expected):
    result = run(deploy(tmp_path, "claude-guardrail-adapter.mjs", body), CLAUDE_INPUT)
    assert claude_decision(result) == expected
    if expected == "deny":
        assert "rm -rf is not allowed" in result.stdout


def test_claude_adapter_gives_the_body_claudes_own_input_untouched(tmp_path):
    echo = tmp_path / "echoed.json"
    adapter = deploy(tmp_path, "claude-guardrail-adapter.mjs", "echo.mjs")
    run(adapter, CLAUDE_INPUT, GUARDRAIL_ECHO=str(echo))
    assert echo.read_text(encoding="utf-8") == CLAUDE_INPUT


@pytest.mark.parametrize("body", ["crash.mjs", "garbage.mjs", "undecided.mjs"])
def test_a_broken_body_is_a_decision_never_a_pass(tmp_path, body):
    """The defect: with the body registered directly, a crash exits 1, which Claude ignores."""
    adapter = deploy(tmp_path, "claude-guardrail-adapter.mjs", body)
    assert claude_decision(run(adapter, CLAUDE_INPUT)) == "ask"  # a person is asked
    assert claude_decision(run(adapter, BYPASS_INPUT)) == "deny"  # nothing else would prompt: it blocks


def test_a_missing_body_blocks_under_bypass(tmp_path):
    adapter = deploy(tmp_path, "claude-guardrail-adapter.mjs", "allow.mjs")
    (adapter.parent / "allow.mjs").unlink()
    assert claude_decision(run(adapter, BYPASS_INPUT)) == "deny"
    assert claude_decision(run(adapter, CLAUDE_INPUT)) == "ask"


def test_a_body_that_hangs_is_cut_off_by_the_adapters_own_timeout_before_claudes(tmp_path):
    adapter = deploy(tmp_path, "claude-guardrail-adapter.mjs", "hang.mjs", timeout=1)
    started = time.monotonic()
    result = run(adapter, BYPASS_INPUT)
    assert time.monotonic() - started < 10  # it did not wait for the body, it answered
    assert claude_decision(result) == "deny"


def test_the_sidecar_can_make_the_failure_strict_without_claude_saying_so(tmp_path):
    adapter = deploy(tmp_path, "claude-guardrail-adapter.mjs", "crash.mjs", strict=True)
    assert claude_decision(run(adapter, CLAUDE_INPUT)) == "deny"


@pytest.mark.parametrize("sidecar_text", ["{not json", '{"hooks": "nope"}', ""])
def test_a_corrupt_sidecar_fails_closed_not_open(tmp_path, sidecar_text):
    adapter = deploy(tmp_path, "claude-guardrail-adapter.mjs", "allow.mjs")
    (adapter.parent / "nexgen-guardrail.config.json").write_text(sidecar_text, encoding="utf-8")
    assert claude_decision(run(adapter, BYPASS_INPUT)) == "deny"


def test_no_sidecar_at_all_means_no_guardrail_and_allows(tmp_path):
    adapter = deploy(tmp_path, "claude-guardrail-adapter.mjs", "allow.mjs")
    (adapter.parent / "nexgen-guardrail.config.json").unlink()
    assert claude_decision(run(adapter, BYPASS_INPUT)) == "allow"
    adapter2 = deploy(tmp_path / "second", "claude-guardrail-adapter.mjs", None) if (tmp_path / "second").mkdir() is None else None
    assert claude_decision(run(adapter2, BYPASS_INPUT)) == "allow"  # a sidecar with no hooks: also nothing to run


def test_unparseable_input_is_not_waved_through(tmp_path):
    adapter = deploy(tmp_path, "claude-guardrail-adapter.mjs", "allow.mjs")
    assert claude_decision(run(adapter, "not json")) == "ask"
    assert claude_decision(run(adapter, "")) == "ask"


def test_a_missing_or_truncated_core_blocks_instead_of_exiting_one(tmp_path):
    """A failed static import exits 1 before any handler runs; 1 lets the command through."""
    adapter = deploy(tmp_path, "claude-guardrail-adapter.mjs", "allow.mjs")
    core = adapter.parent / "nexgen-guardrail-core.mjs"
    core.unlink()
    assert run(adapter, CLAUDE_INPUT).returncode == 2
    core.write_text("export function loadSidecar( {", encoding="utf-8")  # a write cut short
    assert run(adapter, CLAUDE_INPUT).returncode == 2


def test_the_worst_of_several_bodies_wins(tmp_path):
    adapter = deploy(tmp_path, "claude-guardrail-adapter.mjs", "allow.mjs")
    (adapter.parent / "deny.mjs").write_text(BODIES["deny.mjs"], encoding="utf-8")
    config = json.loads((adapter.parent / "nexgen-guardrail.config.json").read_text(encoding="utf-8"))
    config["hooks"].append({"file": str(adapter.parent / "deny.mjs"), "timeout": 5})
    (adapter.parent / "nexgen-guardrail.config.json").write_text(json.dumps(config), encoding="utf-8")
    assert claude_decision(run(adapter, CLAUDE_INPUT)) == "deny"


# ------------------------------------------------------------------ the audit trail


def test_every_consultation_leaves_a_small_record_the_doctor_can_read(tmp_path):
    audit = tmp_path / "state" / "claude.json"
    adapter = deploy(tmp_path, "claude-guardrail-adapter.mjs", "allow.mjs", auditFile=str(audit))
    for _ in range(3):
        run(adapter, CLAUDE_INPUT)
    record = json.loads(audit.read_text(encoding="utf-8"))
    assert record["cli"] == "claude" and record["count"] == 3 and record["last"] == "allow"
    assert abs(record["at"] / 1000 - time.time()) < 60


def test_an_unwritable_audit_path_never_changes_a_decision(tmp_path):
    blocker = tmp_path / "a-file"
    blocker.write_text("x", encoding="utf-8")
    adapter = deploy(tmp_path, "claude-guardrail-adapter.mjs", "deny.mjs", auditFile=str(blocker / "sub" / "claude.json"))
    assert claude_decision(run(adapter, CLAUDE_INPUT)) == "deny"


# ------------------------------------------------------------------ Antigravity


def antigravity_input(command: str = "ls") -> str:
    return json.dumps({"toolCall": {"name": "run_command", "args": {"CommandLine": command}},
                       "workspacePaths": ["/work"], "conversationId": "c-1"})


def antigravity_decision(result: subprocess.CompletedProcess[str]) -> dict:
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize(("body", "expected"), [("allow.mjs", "allow"), ("deny.mjs", "deny"), ("ask.mjs", "ask")])
def test_antigravity_adapter_translates_both_ways(tmp_path, body, expected):
    result = run(deploy(tmp_path, "antigravity-guardrail-adapter.mjs", body), antigravity_input())
    assert antigravity_decision(result)["decision"] == expected


def test_antigravity_adapter_hands_the_body_the_claude_shaped_payload(tmp_path):
    echo = tmp_path / "echoed.json"
    run(deploy(tmp_path, "antigravity-guardrail-adapter.mjs", "echo.mjs"), antigravity_input("git push"),
        GUARDRAIL_ECHO=str(echo))
    payload = json.loads(echo.read_text(encoding="utf-8"))
    assert payload["tool_name"] == "Bash" and payload["tool_input"] == {"command": "git push"}
    assert payload["cwd"] == "/work" and payload["session_id"] == "c-1"


def test_antigravity_broken_body_asks_and_denies_when_strict(tmp_path):
    assert antigravity_decision(run(deploy(tmp_path, "antigravity-guardrail-adapter.mjs", "crash.mjs"),
                                    antigravity_input()))["decision"] == "ask"
    second = tmp_path / "strict"
    second.mkdir()
    adapter = deploy(second, "antigravity-guardrail-adapter.mjs", "crash.mjs", strict=True)
    assert antigravity_decision(run(adapter, antigravity_input()))["decision"] == "deny"


def test_antigravity_unexpected_input_shape_is_not_allowed_unchecked(tmp_path):
    adapter = deploy(tmp_path, "antigravity-guardrail-adapter.mjs", "allow.mjs")
    assert antigravity_decision(run(adapter, json.dumps({"toolCall": {"name": "x", "args": {}}})))["decision"] == "ask"
    assert antigravity_decision(run(adapter, "garbage"))["decision"] == "ask"


def test_antigravity_missing_core_still_answers_deny(tmp_path):
    adapter = deploy(tmp_path, "antigravity-guardrail-adapter.mjs", "allow.mjs")
    (adapter.parent / "nexgen-guardrail-core.mjs").unlink()
    assert antigravity_decision(run(adapter, antigravity_input()))["decision"] == "deny"


# ------------------------------------------------------------------ OpenCode
#
# The plugin is the V2 shape OpenCode 2.0.24 accepts: a default export `{ id, setup(ctx) }` (a bare function,
# the V1 shape, is refused at load), with the two hooks `ctx.shell.hook("create.before")` and
# `ctx.permission.hook("evaluate")`. Both facts, and the event shapes below, were observed live with the real binary.


def opencode_fire(tmp_path: Path, body: str | None, command: str = "ls", effect: str = "ask", **sidecar) -> dict:
    """Loads the plugin the way OpenCode V2 does, fires both hooks for one shell command, returns what they did."""
    adapter = deploy(tmp_path, "opencode-guardrail-plugin.mjs", body, **sidecar)
    driver = tmp_path / "driver.mjs"
    driver.write_text(
        f"import plugin from {json.dumps(adapter.as_uri())};\n"
        "const hooks = {};\n"
        "const ctx = {\n"
        "  shell: { hook: async (name, fn) => { hooks['shell.' + name] = fn; } },\n"
        "  permission: { hook: async (name, fn) => { hooks['permission.' + name] = fn; } },\n"
        "};\n"
        "await plugin.setup(ctx);\n"
        "let shellError = null;\n"
        f"try {{ await hooks['shell.create.before']({{ command: {json.dumps(command)}, cwd: '/work', timeout: 1, shell: '/bin/bash', env: {{}} }}); }}\n"
        "catch (error) { shellError = String(error.message); }\n"
        f"const event = {{ action: 'shell', resources: [{json.dumps(command)}], effect: {json.dumps(effect)}, sessionID: 's1', metadata: {{}} }};\n"
        "await hooks['permission.evaluate'](event);\n"
        "console.log(JSON.stringify({ hooks: Object.keys(hooks).sort(), shellError, effect: event.effect, message: event.message ?? null }));\n",
        encoding="utf-8")
    done = subprocess.run([NODE, str(driver)], capture_output=True, text=True, timeout=60, check=False)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_opencode_plugin_is_a_v2_definition_with_both_hooks(tmp_path):
    fired = opencode_fire(tmp_path, "allow.mjs")
    assert fired["hooks"] == ["permission.evaluate", "shell.create.before"]
    again = tmp_path / "again"
    again.mkdir()
    adapter = deploy(again, "opencode-guardrail-plugin.mjs", "allow.mjs")
    probe = tmp_path / "shape.mjs"
    probe.write_text(
        f"import plugin from {json.dumps(adapter.as_uri())};\n"
        "console.log(JSON.stringify({ type: typeof plugin, id: plugin.id, setup: typeof plugin.setup }));\n", encoding="utf-8")
    shape = json.loads(subprocess.run([NODE, str(probe)], capture_output=True, text=True, timeout=60, check=True).stdout)
    assert shape == {"type": "object", "id": "nexgen-guardrail", "setup": "function"}, "a bare function is the V1 shape and is not loaded"


@pytest.mark.parametrize("effect", ["ask", "allow"])
def test_opencode_a_denied_command_is_stopped_under_any_posture(tmp_path, effect):
    fired = opencode_fire(tmp_path, "deny.mjs", effect=effect)
    assert fired["shellError"] and "rm -rf is not allowed" in fired["shellError"], "the veto does not depend on the rules"
    assert fired["effect"] == "deny" and "rm -rf is not allowed" in fired["message"]


def test_opencode_plugin_ignores_what_is_not_a_shell_command(tmp_path):
    adapter = deploy(tmp_path, "opencode-guardrail-plugin.mjs", "deny.mjs")
    driver = tmp_path / "driver.mjs"
    driver.write_text(
        f"import plugin from {json.dumps(adapter.as_uri())};\n"
        "const hooks = {};\n"
        "await plugin.setup({ shell: { hook: async (n, f) => { hooks[n] = f; } }, permission: { hook: async (n, f) => { hooks[n] = f; } } });\n"
        "const read = { action: 'read', resources: ['/x'], effect: 'ask' };\n"
        "await hooks['evaluate'](read);\n"
        "let thrown = false; try { await hooks['create.before']({ cwd: '/w' }); } catch { thrown = true; }\n"
        "console.log(JSON.stringify({ effect: read.effect, thrown }));\n", encoding="utf-8")
    done = json.loads(subprocess.run([NODE, str(driver)], capture_output=True, text=True, timeout=60, check=True).stdout)
    assert done == {"effect": "ask", "thrown": False}


def test_opencode_allow_answers_only_when_the_engine_opted_the_posture_in(tmp_path):
    """Under an `ask` posture the person asked to be asked: an allow from the body must not remove the prompt."""
    assert opencode_fire(tmp_path, "allow.mjs")["effect"] == "ask"
    second = tmp_path / "mediated"
    second.mkdir()
    fired = opencode_fire(second, "allow.mjs", autoAllow=True)
    assert fired["effect"] == "allow" and fired["shellError"] is None
    third = tmp_path / "already-allowed"
    third.mkdir()
    assert opencode_fire(third, "allow.mjs", effect="allow")["effect"] == "allow"


def test_opencode_never_auto_allows_what_the_body_denies_or_questions(tmp_path):
    for i, (body, effect) in enumerate((("deny.mjs", "deny"), ("ask.mjs", "ask"), ("crash.mjs", "ask"))):
        sub = tmp_path / str(i)
        sub.mkdir()
        fired = opencode_fire(sub, body, autoAllow=True)
        assert fired["effect"] == effect
        # `ask` is the permission system's business: only a deny stops the command at creation.
        assert (fired["shellError"] is not None) == (effect == "deny")


def test_opencode_a_question_never_loosens_an_allow(tmp_path):
    assert opencode_fire(tmp_path, "ask.mjs", effect="allow")["effect"] == "ask"


def test_opencode_broken_guardrail_denies_where_it_is_the_only_brake(tmp_path):
    fired = opencode_fire(tmp_path, "crash.mjs", autoAllow=True, strict=True)
    assert fired["effect"] == "deny" and fired["shellError"]


def test_opencode_corrupt_sidecar_is_not_read_as_no_guardrail(tmp_path):
    adapter = deploy(tmp_path, "opencode-guardrail-plugin.mjs", "allow.mjs", autoAllow=True)
    (adapter.parent / "nexgen-guardrail.config.json").write_text("{broken", encoding="utf-8")
    driver = tmp_path / "driver2.mjs"
    driver.write_text(
        f"import plugin from {json.dumps(adapter.as_uri())};\n"
        "const hooks = {};\n"
        "await plugin.setup({ shell: { hook: async (n, f) => { hooks[n] = f; } }, permission: { hook: async (n, f) => { hooks[n] = f; } } });\n"
        "const event = { action: 'shell', resources: ['ls'], effect: 'ask' };\n"
        "await hooks['evaluate'](event);\n"
        "console.log(JSON.stringify(event.effect));\n", encoding="utf-8")
    done = subprocess.run([NODE, str(driver)], capture_output=True, text=True, timeout=60, check=False)
    assert json.loads(done.stdout) == "ask"  # not "allow", and not left to chance


def test_opencode_missing_core_fails_closed_per_command_and_the_plugin_still_loads(tmp_path):
    adapter = deploy(tmp_path, "opencode-guardrail-plugin.mjs", "allow.mjs", autoAllow=True)
    (adapter.parent / "nexgen-guardrail-core.mjs").unlink()
    driver = tmp_path / "driver3.mjs"
    driver.write_text(
        f"import plugin from {json.dumps(adapter.as_uri())};\n"
        "const hooks = {};\n"
        "await plugin.setup({ shell: { hook: async (n, f) => { hooks[n] = f; } }, permission: { hook: async (n, f) => { hooks[n] = f; } } });\n"
        "const event = { action: 'shell', resources: ['ls'], effect: 'ask' };\n"
        "await hooks['evaluate'](event);\n"
        "console.log(JSON.stringify(event.effect));\n", encoding="utf-8")
    done = subprocess.run([NODE, str(driver)], capture_output=True, text=True, timeout=60, check=False)
    assert done.returncode == 0 and json.loads(done.stdout) == "ask"


def test_opencode_every_command_is_recorded_as_a_consultation(tmp_path):
    """The doctor reads this record to tell a guardrail that works from one that is installed and never called."""
    audit = tmp_path / "audit" / "opencode.json"
    opencode_fire(tmp_path, "allow.mjs", auditFile=str(audit))
    first = json.loads(audit.read_text(encoding="utf-8"))
    assert first["cli"] == "opencode" and first["count"] == 1 and first["last"] == "allow"
    second = tmp_path / "second"
    second.mkdir()
    opencode_fire(second, "deny.mjs", auditFile=str(audit))
    assert json.loads(audit.read_text(encoding="utf-8"))["count"] == 2


def test_the_body_is_run_by_node_even_when_the_host_is_not_node(tmp_path):
    """OpenCode is a compiled bun binary: there `process.execPath` is OpenCode, and spawning it with the body's path
    started a second OpenCode that died on "not a directory". Found only by running the real binary."""
    shutil.copy2(HOOKS / "nexgen-guardrail-core.mjs", tmp_path / "core.mjs")
    driver = tmp_path / "which.mjs"
    driver.write_text(
        "import { nodeBinary } from './core.mjs';\n"
        "console.log(JSON.stringify([\n"
        "  nodeBinary('/srv/tools/.opencode/bin/opencode'),\n"
        "  nodeBinary('/usr/bin/node'),\n"
        "  nodeBinary('C:\\\\Program Files\\\\nodejs\\\\node.exe'),\n"
        "  nodeBinary('/opt/nodejs/bin/nodejs-wrapper'),\n"
        "  nodeBinary('/srv/tools/.bun/bin/bun'),\n"
        "]));\n", encoding="utf-8")
    out = json.loads(subprocess.run([NODE, str(driver)], capture_output=True, text=True, timeout=60, check=True).stdout)
    assert out == ["node", "/usr/bin/node", "C:\\Program Files\\nodejs\\node.exe", "node", "node"]
