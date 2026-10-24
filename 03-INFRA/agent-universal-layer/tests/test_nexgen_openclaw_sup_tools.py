"""`/sup` and the supervision tools: the engine's verbs, and who may call them.

The plugin decides nothing it could ask the engine: each tool turns its arguments
into the argv a person would have typed and returns the lines the engine printed.
These tests drive the real plugin with node and a fake engine, so the argv, the
allowlist and the answer are what the code actually produces.
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
  return {{
    commands: [...commands.keys()],
    tools: [...tools.keys()],
    optional: [...options].filter(([, opts]) => opts && opts.optional).map(([name]) => name),
  }};
}}
"""

#: A fake engine: every call answers with the argv it was given, so the test reads both.
FAKE_ENGINE = """
const calls = [];
function fakeChild(text) {
  const handlers = {};
  const child = {
    stdout: {on: (_name, fn) => { handlers.out = fn; }},
    stderr: {on: () => {}},
    on: (name, fn) => { handlers[name] = fn; },
    kill: () => {},
  };
  setTimeout(() => {
    handlers.out(Buffer.from(text));
    handlers.close(0);
  }, 0);
  return child;
}
const spawnFn = (argv, args, options) => {
  // `args` without the CLI module: the engine's own arguments are what these tests read.
  calls.push({argv, args: args.slice(1), options});
  return fakeChild(JSON.stringify({lines: ["ran: " + args.slice(1).join(" ")]}));
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
    uri = json.dumps((PLUGIN / "index.mjs").as_uri())
    return run_node(tmp_path, PREAMBLE.format(uri=uri) + script)


def a_config(tmp_path: Path) -> Path:
    entry = tmp_path / "engine.mjs"
    entry.write_text("console.log('[]');", encoding="utf-8")
    config = tmp_path / "nexgen.config.json"
    config.write_text(json.dumps({"engine": {"command": NODE, "entry": str(entry)}}), encoding="utf-8")
    return config


def wired(tmp_path: Path) -> str:
    """The registration every tool test needs, on the fake engine."""
    config = a_config(tmp_path)
    return FAKE_ENGINE + f"""
import {{registerSup}} from {json.dumps((PLUGIN / "sup.mjs").as_uri())};
import {{registerSupTools}} from {json.dumps((PLUGIN / "sup-tools.mjs").as_uri())};
const commands = new Map();
const tools = new Map();
const api = {{
  registerCommand: (spec) => commands.set(spec.name, spec),
  registerTool: (tool) => tools.set(tool.name, tool),
}};
registerSup(api, {{configPath: {json.dumps(str(config))}, spawnFn}});
registerSupTools(api, {{configPath: {json.dumps(str(config))}, spawnFn}});
const call = async (name, params) => (await tools.get(name).execute("call-1", params));
const run = async (args) => (await commands.get("sup").handler({{args}}));
"""


# ---------------------------------------------------------------- what is registered


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_plugin_registers_sup_and_eight_supervision_tools(tmp_path):
    fired = drive_plugin(tmp_path, "console.log(JSON.stringify(registered()));\n")

    assert "sup" in fired["commands"]
    assert sorted(name for name in fired["tools"] if name.startswith("nexgen_sup_")) == [
        "nexgen_sup_collect", "nexgen_sup_costs", "nexgen_sup_publish", "nexgen_sup_review",
        "nexgen_sup_run", "nexgen_sup_start", "nexgen_sup_status", "nexgen_sup_unit",
    ]
    # Only the tools that change state wait for an operator's allowlist.
    assert "nexgen_sup_status" not in fired["optional"]
    assert "nexgen_sup_costs" not in fired["optional"]
    assert sorted(fired["optional"]) == [
        "nexgen_sup_collect", "nexgen_sup_publish", "nexgen_sup_review",
        "nexgen_sup_run", "nexgen_sup_start", "nexgen_sup_unit",
    ]


# --------------------------------------------------------------------- /sup


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_sup_without_an_id_asks_for_the_tasks_and_the_spend(tmp_path):
    fired = run_node(tmp_path, wired(tmp_path) + """
const text = (await run("")).text;
console.log(JSON.stringify({text, calls: calls.map((call) => call.args)}));
""")

    assert fired["calls"] == [
        ["local", "sup", "status", "--json"],
        ["local", "sup", "costi", "--json"],
    ]
    assert "ran: local sup status --json" in fired["text"]
    assert "ran: local sup costi --json" in fired["text"]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_sup_with_an_id_asks_for_that_task_only(tmp_path):
    fired = run_node(tmp_path, wired(tmp_path) + """
const text = (await run(" 20261024-abc ")).text;
console.log(JSON.stringify({text, calls: calls.map((call) => call.args)}));
""")

    assert fired["calls"] == [["local", "sup", "status", "20261024-abc", "--json"]]
    assert fired["text"] == "ran: local sup status 20261024-abc --json"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_an_engine_that_cannot_answer_is_one_sentence_not_two(tmp_path):
    """Two read verbs, one sentence: a person reads it once."""
    fired = run_node(tmp_path, f"""
import {{registerSup}} from {json.dumps((PLUGIN / "sup.mjs").as_uri())};
const commands = new Map();
registerSup({{
  registerCommand: (spec) => commands.set(spec.name, spec),
}}, {{configPath: {json.dumps(str(tmp_path / "absent.json"))}}});
const answer = await commands.get("sup").handler({{args: ""}});
console.log(JSON.stringify({{answer}}));
""")

    assert answer_lines(fired["answer"]["text"]) == 1
    assert fired["answer"]["text"].startswith("sup: the engine path is not configured yet")


def answer_lines(text: str) -> int:
    return len([line for line in text.splitlines() if line.strip()])


# ------------------------------------------------------------- the read tools


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_status_tool_asks_for_the_json_lines(tmp_path):
    fired = run_node(tmp_path, wired(tmp_path) + """
const answer = await call("nexgen_sup_status", {id: "t1"});
console.log(JSON.stringify({answer, calls: calls.map((call) => call.args)}));
""")

    assert fired["calls"] == [["local", "sup", "status", "t1", "--json"]]
    assert fired["answer"]["content"][0]["text"] == "ran: local sup status t1 --json"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_costs_tool_asks_for_the_spend_of_one_task(tmp_path):
    fired = run_node(tmp_path, wired(tmp_path) + """
const answer = await call("nexgen_sup_costs", {id: "t1"});
console.log(JSON.stringify({answer, calls: calls.map((call) => call.args)}));
""")

    assert fired["calls"] == [["local", "sup", "costi", "t1", "--json"]]


# ------------------------------------------------------------ the write tools


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_start_tool_refuses_an_empty_objective_without_running_anything(tmp_path):
    fired = run_node(tmp_path, wired(tmp_path) + """
const answer = await call("nexgen_sup_start", {objective: "  "});
console.log(JSON.stringify({answer, calls}));
""")

    assert fired["answer"]["content"][0]["text"] == "sup: I need objective to do that."
    assert fired["calls"] == []


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_unit_tool_passes_every_pattern_and_the_verification(tmp_path):
    fired = run_node(tmp_path, wired(tmp_path) + """
const answer = await call("nexgen_sup_unit", {
  id: "t1", unit: "comandi", repo: "/repo", base: "abc123", brief: "/brief.md",
  allow: ["src/*", "docs/*"], verify: "python -m pytest -q",
});
console.log(JSON.stringify({answer, calls: calls.map((call) => call.args)}));
""")

    assert fired["calls"] == [[
        "local", "sup", "unit", "t1", "comandi",
        "--allow", "src/*", "--allow", "docs/*",
        "--verify", "python -m pytest -q",
        "--repo", "/repo", "--base", "abc123", "--brief", "/brief.md",
    ]]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_review_tool_speaks_one_decision_at_a_time(tmp_path):
    fired = run_node(tmp_path, wired(tmp_path) + """
const approve = await call("nexgen_sup_review", {id: "t1", unit: "u", decision: "approve", reason: "ok", close: true});
const rework = await call("nexgen_sup_review", {id: "t1", unit: "u", decision: "rework", corrections_file: "/fix.md"});
const resend = await call("nexgen_sup_review", {id: "t1", unit: "u", decision: "resend", model: "m"});
const wrong = await call("nexgen_sup_review", {id: "t1", unit: "u", decision: "approve"});
console.log(JSON.stringify({approve, rework, resend, wrong, calls: calls.map((call) => call.args)}));
""")

    assert fired["calls"] == [
        ["local", "sup", "review", "t1", "u", "--approve", "ok", "--close"],
        ["local", "sup", "review", "t1", "u", "--rework", "/fix.md"],
        ["local", "sup", "review", "t1", "u", "--resend", "-m", "m"],
    ]
    assert fired["wrong"]["content"][0]["text"] == "sup: I need reason (why the unit is approved) to do that."


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_publish_tool_replays_onto_a_named_branch_and_pushes_once(tmp_path):
    fired = run_node(tmp_path, wired(tmp_path) + """
const answer = await call("nexgen_sup_publish", {
  id: "t1", unit: "u", repo: "/owner", branch: "developer", remote: "origin", push: true,
});
console.log(JSON.stringify({answer, calls: calls.map((call) => call.args)}));
""")

    assert fired["calls"] == [[
        "local", "sup", "publish", "t1", "u",
        "--repo", "/owner", "--branch", "developer", "--remote", "origin", "--push",
    ]]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_collection_waits_longer_than_a_creation(tmp_path):
    """The collect runs the unit's own tests: its deadline is minutes, not seconds."""
    fired = run_node(tmp_path, """
console.log(JSON.stringify({
  write: (await import({tools})).WRITE_TIMEOUT_MS,
  collect: (await import({tools})).COLLECT_TIMEOUT_MS,
}));
""".replace("{tools}", json.dumps((PLUGIN / "sup-tools.mjs").as_uri())))

    assert fired["collect"] > fired["write"]


# ----------------------------------------------------------------- the names


def test_the_three_places_that_name_the_tools_agree(tmp_path):
    """The manifest, the plugin and the allowlist are one list written three times.

    Nothing else would notice a rename that reached only two of them: the tool would
    silently never be exposed.
    """
    import sys

    SCRIPTS_DIR = REAL_UL.parent.parent / "03-INFRA" / "scripts"
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    from nexgen_core.openclaw.profile import SUPERVISOR_TOOLS

    manifest = json.loads((PLUGIN / "openclaw.plugin.json").read_text(encoding="utf-8"))
    declared = set(manifest["contracts"]["tools"])
    optional_in_manifest = {name for name, meta in manifest["toolMetadata"].items() if meta.get("optional")}
    assert optional_in_manifest == set(SUPERVISOR_TOOLS)

    js = json.loads(subprocess.run(
        [NODE, "-e", (
            "import('" + (PLUGIN / "sup-tools.mjs").as_uri() + "').then((m) => console.log(JSON.stringify("
            "{optional: m.SUPERVISOR_TOOLS, read: m.READ_TOOLS})))"
        )],
        capture_output=True, text=True, encoding="utf-8", timeout=60, check=True,
    ).stdout)
    assert set(js["optional"]) == set(SUPERVISOR_TOOLS)
    assert set(js["optional"]) | set(js["read"]) <= declared
    # The council's two tools are declared too, and neither is an operator opt-in.
    assert {"nexgen_council", "nexgen_council_result"} <= declared
    assert not {"nexgen_council", "nexgen_council_result"} & optional_in_manifest
