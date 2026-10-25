"""The jobs of the `nexgen` plugin, run the way OpenClaw runs them.

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
    "echo.mjs": (
        "import { readFileSync, writeFileSync } from 'node:fs';"
        "writeFileSync(process.env.GUARDRAIL_ECHO, readFileSync(0, 'utf8'));"
    ),
}
PREAMBLE = """
import plugin from {uri};
import {{ dirname, join }} from "node:path";
import {{ fileURLToPath }} from "node:url";
import {{ writeFileSync }} from "node:fs";
const DIR = dirname(fileURLToPath(import.meta.url));
const handlers = new Map();
const commands = new Map();
const logs = [];
const logger = {{
  info: (line) => logs.push(String(line)),
  warn: () => {{}},
  error: () => {{}},
  debug: () => {{}},
}};
plugin.register({{ on: (name, fn) => {{
  if (!handlers.has(name)) handlers.set(name, []);
  handlers.get(name).push(fn);
}}, registerCommand: (spec) => commands.set(spec.name, spec), logger }});
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
async function command(name) {{
  const spec = commands.get(name);
  if (!spec) return "MISSING";
  return spec.handler({{}});
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


# --------------------------------------------------------------- one plugin, every job


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_plugin_registers_the_jobs_with_the_guardrail_first(tmp_path):
    fired = drive(deployed(tmp_path), "console.log(JSON.stringify({id: plugin.id, order: [...handlers.keys()]}));\n")

    assert fired["id"] == "nexgen"
    assert fired["order"][0] == "before_tool_call"
    assert {"after_compaction", "session_start", "before_prompt_build", "skill_changed"} <= set(
        fired["order"],
    )


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_missing_sidecar_blocks_instead_of_allowing(tmp_path):
    """The plugin IS the guardrail here: its sidecar missing is a broken install."""
    target = deployed(tmp_path)
    (target / "nexgen-guardrail.config.json").unlink()

    fired = drive(target, (
        "console.log(JSON.stringify(await call('before_tool_call', "
        "{toolName: 'exec', params: {command: 'ls'}}, {sessionId: 's'})));\n"
    ))

    assert fired["block"] is True
    assert "sidecar" in fired["blockReason"]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_sidecar_with_no_hooks_is_no_guardrail_and_allows(tmp_path):
    """A present, valid sidecar with an empty list is a choice, not a fault."""
    target = deployed(tmp_path)
    (target / "nexgen-guardrail.config.json").write_text('{"hooks": []}', encoding="utf-8")

    fired = drive(target, (
        "console.log(JSON.stringify(await call('before_tool_call', "
        "{toolName: 'exec', params: {command: 'ls'}}, {sessionId: 's'})));\n"
    ))

    assert fired is None


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


# --------------------------------------------------------------------- the Workshop


def workshop_event(action: str = "created", source: str = "workshop", named: bool = True) -> dict:
    """The event OpenClaw fires after a committed live-skill change, in the SDK's own shape."""
    artifact = {
        "skillFile": "/state/agents/main/agent/workshop-skills/release-notes/SKILL.md",
        "skillDir": "/state/agents/main/agent/workshop-skills/release-notes",
        "source": "workshop",
        "revision": {"contentSha256": "0" * 64, "treeSha256": "0" * 64},
    }
    if named:
        artifact["name"] = "release-notes"
        artifact["skillKey"] = "workshop:release-notes"
    event = {"action": action, "source": source, "occurredAt": "2026-10-24T00:00:00Z"}
    event["before" if action == "removed" else "after"] = artifact
    return event


def fire_workshop(tmp_path: Path, event: dict) -> dict:
    """Fires one `skill_changed`; returns the handler's answer and what reached the gateway log."""
    return drive(deployed(tmp_path), f"""
const answer = await call("skill_changed", {json.dumps(event)}, {{agentId: "main", workspaceDir: "/w"}});
console.log(JSON.stringify({{answer, logs}}));
""")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_new_workshop_skill_is_announced_with_the_command_that_adopts_it(tmp_path):
    fired = fire_workshop(tmp_path, workshop_event())

    assert fired["answer"] is None  # an observer, never a decision
    assert fired["logs"] == [
        'new skill in the Workshop: "release-notes". run `nexgen openclaw adopt` to make it canonical',
    ]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_workshop_skill_that_changed_is_announced_too(tmp_path):
    fired = fire_workshop(tmp_path, workshop_event(action="updated"))

    assert len(fired["logs"]) == 1 and "release-notes" in fired["logs"][0]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_skill_installed_by_another_source_is_not_announced(tmp_path):
    fired = fire_workshop(tmp_path, workshop_event(source="clawhub"))

    assert fired["logs"] == []


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_removal_leaves_nothing_to_adopt_and_is_silent(tmp_path):
    fired = fire_workshop(tmp_path, workshop_event(action="removed"))

    assert fired["logs"] == []


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_skill_is_named_whichever_side_of_the_event_carries_it(tmp_path):
    """A removal keeps the artifact in `before`; the name is read from there too, not from `after`."""
    target = deployed(tmp_path)
    driver = target / "workshop-driver.mjs"
    script = f'''import {{ adoptionNote }} from {json.dumps((target / "workshop.mjs").as_uri())};
console.log(JSON.stringify({{
  after: adoptionNote({{action: "updated", source: "workshop", after: {{name: "after-skill"}}}}),
  before: adoptionNote({{action: "updated", source: "workshop", before: {{name: "before-skill"}}}}),
  neither: adoptionNote({{action: "updated", source: "workshop"}}),
}}));
'''
    driver.write_text(script, encoding="utf-8")
    done = subprocess.run(
        [NODE, str(driver)], capture_output=True, text=True, encoding="utf-8", timeout=60, check=False,
    )

    assert done.returncode == 0, done.stderr
    notes = json.loads(done.stdout)
    assert "after-skill" in notes["after"]
    assert "before-skill" in notes["before"]
    assert notes["neither"] == ""


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_an_event_without_a_name_is_silent_instead_of_announcing_nothing(tmp_path):
    fired = fire_workshop(tmp_path, workshop_event(named=False))

    assert fired["logs"] == []


# ------------------------------------------------------------------------ posture


def fire_command(tmp_path: Path, posture: str | None, body: str, tool: str = "exec",
                 params: dict | None = None, env: dict | None = None) -> dict | None:
    config = {"posture": posture} if posture else None
    target = deployed(tmp_path, body=body, config=config)
    event = json.dumps({"toolName": tool, "params": params if params is not None else {"command": "ls"}})
    return drive(target, f"console.log(JSON.stringify(await call('before_tool_call', {event}, {{sessionId: 's'}})));\n",
                 env=env)


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


# ------------------------------------------------- the command, however it arrives


@pytest.mark.parametrize("tool", ["bash", "sh", "powershell", "cmd", "run_command", "Bash"])
@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_every_known_shell_alias_is_judged(tmp_path, tool):
    """A tool that runs a shell under another name is still a shell."""
    decision = fire_command(tmp_path, "bypass", "deny.mjs", tool=tool, params={"command": "rm -rf /"})
    assert decision["block"] is True


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_command_given_as_a_list_is_judged_as_one_line(tmp_path):
    decision = fire_command(tmp_path, "bypass", "deny.mjs", tool="exec",
                            params={"command": ["rm", "-rf", "/"]})
    assert decision["block"] is True


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_command_split_between_command_and_args_is_judged_whole(tmp_path):
    echo = tmp_path / "payload.json"
    decision = fire_command(
        tmp_path, "bypass", "echo.mjs", tool="exec",
        params={"command": "rm", "args": ["-rf", "/"]},
        env=dict(os.environ, GUARDRAIL_ECHO=str(echo)),
    )

    assert decision is None
    payload = json.loads(echo.read_text(encoding="utf-8"))
    assert payload["tool_input"]["command"] == "rm -rf /"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_split_command_is_blocked_when_the_body_denies_it(tmp_path):
    """The echo test above proves the text; this one proves the decision."""
    decision = fire_command(
        tmp_path, "bypass", "deny.mjs", tool="exec",
        params={"command": "rm", "args": ["-rf", "/"]},
    )
    assert decision["block"] is True


@pytest.mark.parametrize(("action", "field", "value"), [
    ("write", "data", "rm -rf /"),
    ("paste", "text", "rm -rf /"),
    ("send-keys", "literal", "rm -rf /"),
    ("send-keys", "keys", ["rm", "-rf", "/"]),
])
@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_command_sent_into_a_shell_session_is_judged(tmp_path, action, field, value):
    """`process` writes into a running shell; those payloads are the command."""
    decision = fire_command(
        tmp_path, "bypass", "deny.mjs", tool="process",
        params={"action": action, "sessionId": "s", field: value},
    )
    assert decision["block"] is True


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_terminal_input_is_judged(tmp_path):
    decision = fire_command(
        tmp_path, "bypass", "deny.mjs", tool="terminal",
        params={"action": "input", "data": "rm -rf /\n"},
    )
    assert decision["block"] is True


@pytest.mark.parametrize("action", ["poll", "log", "list", "kill"])
@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_read_only_session_action_is_not_judged_or_asked(tmp_path, action):
    decision = fire_command(
        tmp_path, "ask", "deny.mjs", tool="process", params={"action": action, "sessionId": "s"},
    )
    assert decision is None


@pytest.mark.parametrize("posture", ["ask", "accept-edits"])
@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_shell_call_the_guardrail_cannot_read_asks_for_approval(tmp_path, posture):
    decision = fire_command(
        tmp_path, posture, "allow.mjs", tool="process", params={"action": "write", "sessionId": "s"},
    )
    assert "requireApproval" in decision


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_an_unreadable_shell_call_is_left_alone_under_bypass(tmp_path):
    decision = fire_command(
        tmp_path, "bypass", "allow.mjs", tool="process", params={"action": "write", "sessionId": "s"},
    )
    assert decision is None


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_file_write_asks_under_the_ask_posture(tmp_path):
    decision = fire_command(
        tmp_path, "ask", "allow.mjs", tool="file_write", params={"path": "/tmp/x", "data": "y"},
    )
    assert "requireApproval" in decision
# -------------------------------------------------------------------------- /quota


def fake_engine(target: Path, source: str) -> dict:
    """An entry the plugin runs exactly as it runs the real one: interpreter, module, arguments."""
    script = target / "fake-engine.mjs"
    script.write_text(source, encoding="utf-8")
    return {"command": NODE, "entry": str(script)}


def a_quota_plugin(tmp_path: Path, source: str | None) -> Path:
    """The deployed plugin plus, or without, the `engine` section the deposit writes."""
    target = deployed(tmp_path)
    if source is not None:
        (target / "nexgen.config.json").write_text(
            json.dumps({"engine": fake_engine(target, source)}), encoding="utf-8",
        )
    return target


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_plugin_registers_the_quota_command(tmp_path):
    fired = drive(deployed(tmp_path), """
console.log(JSON.stringify([...commands.values()].map((spec) => ({name: spec.name, description: spec.description}))));
""")

    quota = [spec for spec in fired if spec["name"] == "quota"]
    assert len(quota) == 1
    assert quota[0]["description"]  # a command with no description is a command nobody finds


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_quota_forwards_the_lines_the_engine_already_rendered(tmp_path):
    """The numbers are printed once, by the engine: the chat and the terminal cannot disagree."""
    target = a_quota_plugin(tmp_path, (
        "console.log(JSON.stringify({lines: ['Codex 59% (30d)', 'Claude Code limite non leggibile in locale']}));"
    ))

    fired = drive(target, "console.log(JSON.stringify(await command('quota')));\n")

    assert fired == {"text": "Codex 59% (30d)\nClaude Code limite non leggibile in locale"}


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_plugin_deposited_before_the_engine_section_asks_for_a_sync(tmp_path):
    target = a_quota_plugin(tmp_path, None)

    fired = drive(target, "console.log(JSON.stringify(await command('quota')));\n")

    assert "the engine path is not configured yet" in fired["text"]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_an_engine_that_fails_is_a_sentence_and_never_a_stack_trace(tmp_path):
    target = a_quota_plugin(tmp_path, 'console.error("Traceback (most recent call last):"); process.exit(1);')

    fired = drive(target, "console.log(JSON.stringify(await command('quota')));\n")

    assert fired["text"].startswith("quota: the engine answered with an error")
    assert "Traceback" not in fired["text"]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_plain_text_answer_is_forwarded_as_it_is(tmp_path):
    target = a_quota_plugin(tmp_path, 'console.log("quota: no reading on this machine");')

    fired = drive(target, "console.log(JSON.stringify(await command('quota')));\n")

    assert fired["text"] == "quota: no reading on this machine"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_json_without_lines_is_forwarded_verbatim_rather_than_invented(tmp_path):
    """An engine that answers in some other shape still gets heard, unformatted."""
    target = a_quota_plugin(tmp_path, 'console.log(JSON.stringify({report: "ok"}));')

    fired = drive(target, "console.log(JSON.stringify(await command('quota')));\n")

    assert fired["text"] == '{"report":"ok"}'


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_hung_engine_is_abandoned_with_a_sentence(tmp_path):
    target = a_quota_plugin(tmp_path, "setTimeout(() => {}, 60000);")

    fired = drive(target, f"""
const quota = await import({json.dumps((target / "quota.mjs").as_uri())});
const text = await quota.quotaText(join(DIR, "nexgen.config.json"), {{ timeoutMs: 400 }});
console.log(JSON.stringify({{ text }}));
""")

    assert fired["text"].startswith("quota: the engine did not answer in time")


# --------------------------------------------------------------------- /spend


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_plugin_registers_the_spend_command(tmp_path):
    fired = drive(deployed(tmp_path), """
console.log(JSON.stringify([...commands.values()].map((spec) => ({name: spec.name, description: spec.description}))));
""")

    spend = [spec for spec in fired if spec["name"] == "spend"]
    assert len(spend) == 1
    assert spend[0]["description"]  # a command with no description is a command nobody finds


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_spend_asks_the_engine_for_nexgen_usage_and_forwards_its_lines(tmp_path):
    """The numbers are printed once, by `nexgen usage --json`: the chat forwards them."""
    target = a_quota_plugin(tmp_path, (
        "if (process.argv[2] !== 'usage' || process.argv[3] !== '--json') { console.error(process.argv.slice(2)); process.exit(2); }\n"
        "console.log(JSON.stringify({lines: ['OpenCode Go: 2.00 $ in the last 30d', 'Claude Code: 150 tokens in the last 5h']}));"
    ))

    fired = drive(target, "console.log(JSON.stringify(await command('spend')));\n")

    assert fired == {"text": "OpenCode Go: 2.00 $ in the last 30d\nClaude Code: 150 tokens in the last 5h"}


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_spend_leaves_openclaws_own_usage_command_alone(tmp_path):
    """`/usage` is OpenClaw's footer and its own cost summary: the engine answers beside it."""
    fired = drive(deployed(tmp_path), "console.log(JSON.stringify([...commands.keys()]));\n")

    assert "spend" in fired
    assert "usage" not in fired
