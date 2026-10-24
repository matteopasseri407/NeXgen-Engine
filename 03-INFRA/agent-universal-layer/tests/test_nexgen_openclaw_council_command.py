"""`/council` from a chat, and reading the run back afterwards.

A council takes minutes, so the command answers at once and the engine runs
detached. These tests drive the real plugin with node: the command and the tools
are registered on a fake `api`, the engine is started by a recorder instead of a
real one, and the outcome is read from the log the run itself writes. Nothing here
invokes a seat or a model.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import REAL_UL

PLUGIN = REAL_UL / "hooks" / "openclaw-guardrail-plugin"
NODE = shutil.which("node")

#: The plugin as OpenClaw sees it: a command surface and a tool surface.
PREAMBLE = """
import plugin from {uri};
const commands = new Map();
const tools = new Map();
const options = new Map();
plugin.register({{
  on: () => {{}},
  registerCommand: (spec) => commands.set(spec.name, spec),
  registerTool: (tool, opts) => {{ tools.set(tool.name, tool); options.set(tool.name, opts); }},
}});
export async function command(name, args) {{
  const spec = commands.get(name);
  if (!spec) return "MISSING";
  return spec.handler({{args}});
}}
export async function tool(name, params) {{
  const spec = tools.get(name);
  if (!spec) return "MISSING";
  return spec.execute("call-1", params);
}}
export function registered() {{
  return {{commands: [...commands.keys()], tools: [...tools.keys()], options: Object.fromEntries([...options])}};
}}
"""

#: A `spawn` that starts nothing and answers like a child that did.
RECORDER = """
const started = [];
const spawnFn = (argv, args, options) => {
  started.push({argv, args, options});
  return {pid: 4242, unref: () => {}};
};
"""


def run_node(tmp_path: Path, source: str):
    driver = tmp_path / "driver.mjs"
    driver.write_text(source, encoding="utf-8")
    done = subprocess.run(
        [NODE, str(driver)], capture_output=True, text=True, encoding="utf-8", timeout=120, check=False,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def drive_plugin(tmp_path: Path, script: str):
    """Run `script` with the real plugin registered on a fake `api`."""
    return run_node(tmp_path, PREAMBLE.format(uri=json.dumps((PLUGIN / "index.mjs").as_uri())) + RECORDER + script)


def council_uri() -> str:
    return json.dumps((PLUGIN / "council.mjs").as_uri())


def a_config(tmp_path: Path) -> Path:
    """The switches the deposit writes, with an engine that is node and a script."""
    entry = tmp_path / "engine.mjs"
    entry.write_text("console.log('[]');", encoding="utf-8")
    config = tmp_path / "nexgen.config.json"
    config.write_text(json.dumps({"engine": {"command": NODE, "entry": str(entry)}}), encoding="utf-8")
    return config


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_plugin_registers_the_council_command_and_its_two_tools(tmp_path):
    fired = drive_plugin(tmp_path, "console.log(JSON.stringify(registered()));\n")

    assert "council" in fired["commands"]
    assert {"nexgen_council", "nexgen_council_result"} <= set(fired["tools"])
    # Neither is an operator opt-in: asking the council is not a state change.
    assert fired["options"].get("nexgen_council") is None
    assert fired["options"].get("nexgen_council_result") is None


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_question_is_answered_at_once_and_the_engine_starts_detached(tmp_path):
    config = a_config(tmp_path)
    log_dir = tmp_path / "logs"

    fired = run_node(tmp_path, f"""
const council = await import({council_uri()});
{RECORDER}
const answer = await council.startCouncil("should we cache at the edge?", {{
  configPath: {json.dumps(str(config))}, logDir: {json.dumps(str(log_dir))},
  spawnFn, tag: "council-test", now: new Date("2026-10-24T10:00:00Z"),
}});
console.log(JSON.stringify({{answer, started}}));
""")

    entry = str(tmp_path / "engine.mjs")
    assert fired["answer"]["ok"] is True
    assert fired["answer"]["id"] == "council-test"
    assert fired["started"][0]["args"] == [
        entry, "council", "brainstorm", "should we cache at the edge?",
        "--auto", "--keep-session", "--notify", "--tag", "council-test",
    ]
    assert fired["started"][0]["options"]["detached"] is True
    assert fired["started"][0]["options"]["shell"] is False
    assert fired["answer"]["log"] == str(log_dir / "council-test.log")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_command_handler_answers_with_the_identifier_and_the_log(tmp_path):
    config = a_config(tmp_path)
    log_dir = tmp_path / "logs"

    fired = run_node(tmp_path, f"""
const council = await import({council_uri()});
{RECORDER}
const commands = new Map();
council.registerCouncil({{
  registerCommand: (spec) => commands.set(spec.name, spec),
  registerTool: () => {{}},
}}, {{configPath: {json.dumps(str(config))}, logDir: {json.dumps(str(log_dir))}, spawnFn}});
const answer = await commands.get("council").handler({{args: "is this plan safe?"}});
console.log(JSON.stringify({{answer}}));
""")

    text = fired["answer"]["text"]
    assert text.startswith("council council-")
    assert "I will write to you when it has finished" in text
    assert f"log: {log_dir}" in text


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_an_empty_question_is_a_sentence_and_starts_nothing(tmp_path):
    config = a_config(tmp_path)

    fired = run_node(tmp_path, f"""
const council = await import({council_uri()});
let spawned = 0;
const spawnFn = () => {{ spawned += 1; return {{pid: 1, unref: () => {{}}}}; }};
const answer = await council.startCouncil("   ", {{
  configPath: {json.dumps(str(config))}, logDir: {json.dumps(str(tmp_path / "logs"))}, spawnFn,
}});
console.log(JSON.stringify({{answer, spawned}}));
""")

    assert fired["answer"]["ok"] is False
    assert "ask a question" in fired["answer"]["text"]
    assert fired["spawned"] == 0


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_without_an_engine_the_answer_says_what_to_run(tmp_path):
    fired = run_node(tmp_path, f"""
const council = await import({council_uri()});
const answer = await council.startCouncil("a question", {{
  configPath: {json.dumps(str(tmp_path / "absent.json"))},
  logDir: {json.dumps(str(tmp_path / "logs"))},
}});
console.log(JSON.stringify({{answer}}));
""")

    assert fired["answer"]["text"].startswith("council: the engine path is not configured yet")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_tool_returns_the_identifier_a_later_read_needs(tmp_path):
    config = a_config(tmp_path)
    log_dir = tmp_path / "logs"

    fired = run_node(tmp_path, f"""
const council = await import({council_uri()});
{RECORDER}
const tool = council.councilTool({{
  configPath: {json.dumps(str(config))}, logDir: {json.dumps(str(log_dir))}, spawnFn,
}});
const answer = await tool.execute("call-1", {{question: "what should we do?"}});
console.log(JSON.stringify({{answer}}));
""")

    text = fired["answer"]["content"][0]["text"]
    assert fired["answer"]["content"][0]["type"] == "text"
    assert text.startswith("council council-")
    assert fired["answer"]["details"]["council"].startswith("council-")
    assert fired["answer"]["details"]["log"].endswith(".log")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_run_that_has_not_ended_says_it_is_still_running(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "council-x.log").write_text(
        "# council council-x started 2026-10-24T10:00:00Z\n[council] mode: consult — seats: 2\n",
        encoding="utf-8",
    )

    fired = run_node(tmp_path, f"""
const council = await import({council_uri()});
console.log(JSON.stringify({{text: council.resultOf("council-x", {{logDir: {json.dumps(str(log_dir))}}})}}));
""")

    assert fired["text"].startswith("council council-x: still running.")
    assert "seats: 2" in fired["text"]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_finished_run_quotes_its_verdict_and_the_directory_it_kept(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "council-y.log").write_text(
        "# council council-y started 2026-10-24T10:00:00Z\n"
        "[council] session kept: /state/council/sessions/council-y-20261024T100000Z\n"
        "[council] final verdict: APPROVE\n"
        "/state/council/sessions/council-y-20261024T100000Z/verdict.md\n"
        "[council] notified: Council brainstorm finished — Request: council-y. Outcome: APPROVE.\n",
        encoding="utf-8",
    )

    fired = run_node(tmp_path, f"""
const council = await import({council_uri()});
console.log(JSON.stringify({{text: council.resultOf("council-y", {{logDir: {json.dumps(str(log_dir))}}})}}));
""")

    text = fired["text"]
    assert text.startswith("council council-y: finished.")
    assert "final verdict: APPROVE" in text
    assert "council/sessions/council-y-20261024T100000Z" in text
    assert "# council council-y started" not in text  # the header is ours, not the run's


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_result_tool_answers_what_the_log_holds(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "council-z.log").write_text(
        "[council] notify failed before the end\n[council] notified: done\n", encoding="utf-8",
    )

    fired = run_node(tmp_path, f"""
const council = await import({council_uri()});
const tool = council.councilResultTool({{logDir: {json.dumps(str(log_dir))}}});
const answer = await tool.execute("call-1", {{council_id: "council-z"}});
console.log(JSON.stringify({{answer}}));
""")

    assert fired["answer"]["content"][0]["text"].startswith("council council-z: finished.")
    assert fired["answer"]["details"]["council"] == "council-z"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_an_identifier_nobody_ran_is_said_plainly(tmp_path):
    fired = run_node(tmp_path, f"""
const council = await import({council_uri()});
console.log(JSON.stringify({{text: council.resultOf("council-nope", {{logDir: {json.dumps(str(tmp_path / "logs"))}}})}}));
""")

    assert fired["text"] == "council: no run with that identifier on this machine."
