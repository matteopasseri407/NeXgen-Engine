"""The two jobs of the `nexgen` plugin, run the way OpenClaw runs them.

These tests load the real plugin with node, register it against a fake `api.on` and fire the
hooks OpenClaw documents: `after_compaction`, `session_start`, `before_prompt_build`,
`before_tool_call`. Nothing is asserted from a reading of the source: every
decision below is what the plugin actually answered.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import REAL_UL

HOOKS = REAL_UL / "hooks"
PLUGIN_SOURCE = HOOKS / "openclaw-guardrail-plugin"
BRIEFING_TEXT = (HOOKS / "vault-briefing.txt").read_text(encoding="utf-8").strip()
NODE = shutil.which("node")

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
}
PREAMBLE = """
import plugin from {uri};
const handlers = new Map();
plugin.register({{ on: (name, fn) => {{
  if (!handlers.has(name)) handlers.set(name, []);
  handlers.get(name).push(fn);
}} }});
async function call(name, event, ctx) {{
  const list = handlers.get(name) || [];
  if (!list.length) return "MISSING";
  let answer = null;
  for (const handler of list) {{
    const result = await handler(event, ctx);
    if (result !== undefined && answer === null) answer = result;
  }}
  return answer;
}}
const settle = () => new Promise((resolve) => setTimeout(resolve, 300));
"""


def deployed(tmp_path: Path, body: str = "allow.mjs", config: dict | None = None) -> Path:
    """The plugin as the guard deposits it: every file, the core, the body, the sidecar."""
    target = tmp_path / "deployed"
    target.mkdir()
    for source in sorted(PLUGIN_SOURCE.iterdir()):
        if source.is_file():
            shutil.copy2(source, target / source.name)
    shutil.copy2(HOOKS / "nexgen-guardrail-core.mjs", target / "nexgen-guardrail-core.mjs")
    shutil.copy2(HOOKS / "vault-briefing.txt", target / "vault-briefing.txt")
    (target / "body.mjs").write_text(BODIES[body], encoding="utf-8")
    (target / "nexgen-guardrail.config.json").write_text(
        json.dumps({"hooks": [{"file": str(target / "body.mjs"), "timeout": 5}]}), encoding="utf-8",
    )
    if config is not None:
        (target / "nexgen.config.json").write_text(json.dumps(config), encoding="utf-8")
    return target


def drive(target: Path, script: str, env: dict | None = None):
    """Runs `script` after the preamble, in a fresh node process, and returns its JSON."""
    driver = target / "driver.mjs"
    driver.write_text(
        PREAMBLE.format(uri=json.dumps((target / "index.mjs").as_uri())) + script,
        encoding="utf-8",
    )
    done = subprocess.run(
        [NODE, str(driver)], capture_output=True, text=True, timeout=60, check=False, env=env,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


# ------------------------------------------------------------- one plugin, two jobs


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_plugin_registers_the_two_jobs_with_the_guardrail_first(tmp_path):
    fired = drive(deployed(tmp_path), "console.log(JSON.stringify({id: plugin.id, order: [...handlers.keys()]}));\n")

    assert fired["id"] == "nexgen"
    assert fired["order"][0] == "before_tool_call"
    assert {"after_compaction", "session_start", "before_prompt_build"} <= set(fired["order"])


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_plugin_loads_even_with_no_sidecar_and_no_config(tmp_path):
    target = deployed(tmp_path)
    (target / "nexgen-guardrail.config.json").unlink()

    fired = drive(target, (
        "console.log(JSON.stringify(await call('before_tool_call', "
        "{toolName: 'exec', params: {command: 'ls'}}, {sessionId: 's'})));\n"
    ))

    assert fired is None  # no body, no decision: the call is left alone


# ----------------------------------------------------------------------- briefing


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_compaction_briefs_the_next_prompt_exactly_once(tmp_path):
    target = deployed(tmp_path, config={"briefing": {"enabled": True}})

    fired = drive(target, """
const out = [];
out.push(await call("before_prompt_build", {prompt: "a", messages: []}, {sessionId: "s1"}));
await call("after_compaction", {messageCount: 4}, {sessionId: "s1"});
out.push(await call("before_prompt_build", {prompt: "b", messages: []}, {sessionId: "s1"}));
out.push(await call("before_prompt_build", {prompt: "c", messages: []}, {sessionId: "s1"}));
console.log(JSON.stringify(out));
""")

    assert fired[0] is None
    assert fired[1] == {"prependContext": BRIEFING_TEXT}
    assert fired[2] is None


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_fresh_session_is_never_briefed(tmp_path):
    target = deployed(tmp_path, config={"briefing": {"enabled": True}})

    fired = drive(target, """
await call("session_start", {sessionId: "s2"}, {sessionId: "s2"});
console.log(JSON.stringify(await call("before_prompt_build", {prompt: "a", messages: []}, {sessionId: "s2"})));
""")

    assert fired is None


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_resumed_session_is_briefed_on_its_next_prompt(tmp_path):
    target = deployed(tmp_path, config={"briefing": {"enabled": True}})

    fired = drive(target, """
await call("session_start", {sessionId: "s2", resumedFrom: "s1"}, {sessionId: "s2"});
console.log(JSON.stringify(await call("before_prompt_build", {prompt: "a", messages: []}, {sessionId: "s2"})));
""")

    assert fired == {"prependContext": BRIEFING_TEXT}


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_briefing_is_off_until_the_engine_switches_it_on(tmp_path):
    target = deployed(tmp_path)  # no nexgen.config.json at all

    fired = drive(target, """
await call("after_compaction", {messageCount: 4}, {sessionId: "s1"});
console.log(JSON.stringify(await call("before_prompt_build", {prompt: "a", messages: []}, {sessionId: "s1"})));
""")

    assert fired is None


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_missing_briefing_text_never_breaks_the_prompt(tmp_path):
    target = deployed(tmp_path, config={"briefing": {"enabled": True}})
    (target / "vault-briefing.txt").unlink()

    fired = drive(target, """
await call("after_compaction", {messageCount: 4}, {sessionId: "s1"});
console.log(JSON.stringify(await call("before_prompt_build", {prompt: "a", messages: []}, {sessionId: "s1"})));
""")

    assert fired is None


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_briefing_is_the_very_text_claude_code_injects(tmp_path):
    """One text, two hosts: the plugin reads the same file the checkpoint hook reads."""
    target = deployed(tmp_path, config={"briefing": {"enabled": True}})
    fired = drive(target, """
await call("after_compaction", {messageCount: 4}, {sessionId: "s1"});
console.log(JSON.stringify(await call("before_prompt_build", {prompt: "a", messages: []}, {sessionId: "s1"})));
""")
    hook_dir = tmp_path / "claude"
    hook_dir.mkdir()
    shutil.copy2(HOOKS / "claude-vault-checkpoint.mjs", hook_dir / "claude-vault-checkpoint.mjs")
    shutil.copy2(HOOKS / "vault-briefing.txt", hook_dir / "vault-briefing.txt")
    done = subprocess.run(
        [NODE, str(hook_dir / "claude-vault-checkpoint.mjs")],
        input=json.dumps({"hook_event_name": "SessionStart", "source": "compact"}),
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert done.returncode == 0, done.stderr
    claude_context = json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"]

    assert fired["prependContext"] == claude_context == BRIEFING_TEXT


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_checkpoint_hook_stays_silent_on_a_fresh_start(tmp_path):
    hook_dir = tmp_path / "claude"
    hook_dir.mkdir()
    shutil.copy2(HOOKS / "claude-vault-checkpoint.mjs", hook_dir / "claude-vault-checkpoint.mjs")
    shutil.copy2(HOOKS / "vault-briefing.txt", hook_dir / "vault-briefing.txt")

    done = subprocess.run(
        [NODE, str(hook_dir / "claude-vault-checkpoint.mjs")],
        input=json.dumps({"hook_event_name": "SessionStart", "source": "startup"}),
        capture_output=True, text=True, timeout=60, check=False,
    )

    assert done.returncode == 0 and done.stdout.strip() == ""


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_checkpoint_hook_without_its_text_file_breaks_nothing(tmp_path):
    hook_dir = tmp_path / "claude"
    hook_dir.mkdir()
    shutil.copy2(HOOKS / "claude-vault-checkpoint.mjs", hook_dir / "claude-vault-checkpoint.mjs")
    # The hook also looks in the installed engine: a real home that carries the text would answer.
    home = tmp_path / "home"
    env = {k: v for k, v in os.environ.items() if k != "AGENT_ENGINE_ROOT"}
    env.update(HOME=str(home), USERPROFILE=str(home))

    done = subprocess.run(
        [NODE, str(hook_dir / "claude-vault-checkpoint.mjs")],
        input=json.dumps({"hook_event_name": "SessionStart", "source": "compact"}),
        capture_output=True, text=True, timeout=60, check=False, env=env,
    )

    assert done.returncode == 0 and done.stdout.strip() == ""


# ------------------------------------------------------------------------ posture


def fire_command(tmp_path: Path, posture: str | None, body: str, tool: str = "exec",
                 params: dict | None = None) -> dict | None:
    config = {"posture": posture} if posture else None
    target = deployed(tmp_path, body=body, config=config)
    event = json.dumps({"toolName": tool, "params": params if params is not None else {"command": "ls"}})
    return drive(target, f"console.log(JSON.stringify(await call('before_tool_call', {event}, {{sessionId: 's'}})));\n")


@pytest.mark.parametrize("posture", [None, "bypass"])
@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_bypass_lets_what_the_body_permits_run_without_a_question(tmp_path, posture):
    assert fire_command(tmp_path, posture, "allow.mjs") is None


@pytest.mark.parametrize("posture", [None, "bypass"])
@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_bypass_still_asks_when_the_body_asks(tmp_path, posture):
    decision = fire_command(tmp_path, posture, "ask.mjs")
    assert "requireApproval" in decision
    assert "looks risky" in decision["requireApproval"]["description"]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_bypass_still_blocks_what_the_body_denies(tmp_path):
    decision = fire_command(tmp_path, "bypass", "deny.mjs")
    assert decision["block"] is True
    assert "rm -rf is not allowed" in decision["blockReason"]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_accept_edits_still_confirms_a_shell_command_the_body_permits(tmp_path):
    decision = fire_command(tmp_path, "accept-edits", "allow.mjs")
    assert "requireApproval" in decision


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_accept_edits_does_not_confirm_a_file_write(tmp_path):
    decision = fire_command(tmp_path, "accept-edits", "allow.mjs", tool="write",
                            params={"path": "/tmp/x", "content": "y"})
    assert decision is None


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_accept_edits_still_blocks_a_denied_command(tmp_path):
    assert fire_command(tmp_path, "accept-edits", "deny.mjs")["block"] is True


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_ask_confirms_every_shell_command_the_body_permits(tmp_path):
    assert "requireApproval" in fire_command(tmp_path, "ask", "allow.mjs")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_ask_still_blocks_a_denied_command(tmp_path):
    assert fire_command(tmp_path, "ask", "deny.mjs")["block"] is True


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_ask_also_confirms_a_file_write(tmp_path):
    decision = fire_command(tmp_path, "ask", "allow.mjs", tool="apply_patch",
                            params={"patch": "*** Begin Patch"})
    assert "requireApproval" in decision


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_read_is_never_judged_under_any_posture(tmp_path):
    assert fire_command(tmp_path, "ask", "allow.mjs", tool="read", params={"path": "/tmp/x"}) is None
