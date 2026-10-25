"""Tests for the OpenClaw adopt plugin job (adopt.mjs).

Exercises the `/adopt` chat command and the `skill_changed` lifecycle observer.
Drives the plugin with Node, using a mock api and fake engine configuration.
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


def adopt_uri() -> str:
    return json.dumps((PLUGIN / "adopt.mjs").as_uri())


def index_uri() -> str:
    return json.dumps((PLUGIN / "index.mjs").as_uri())


def a_config(tmp_path: Path, output_text: str = '{"ok": true, "lines": ["Adopted 1 item."]}') -> Path:
    """Fake engine that returns the requested JSON."""
    entry = tmp_path / "fake_engine.mjs"
    entry.write_text(f"console.log({json.dumps(output_text)});\n", encoding="utf-8")
    config = tmp_path / "nexgen.config.json"
    config.write_text(json.dumps({"engine": {"command": NODE, "entry": str(entry)}}), encoding="utf-8")
    return config


def run_node(tmp_path: Path, source: str) -> dict:
    driver = tmp_path / "driver.mjs"
    driver.write_text(source, encoding="utf-8")
    done = subprocess.run(
        [NODE, str(driver)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_adopt_command_is_registered_in_plugin(tmp_path: Path):
    fired = run_node(tmp_path, f"""
const plugin = (await import({index_uri()})).default;
const commands = new Map();
const hooks = new Map();
plugin.register({{
  registerCommand: (spec) => commands.set(spec.name, spec),
  on: (hook, fn) => hooks.set(hook, fn),
}});
console.log(JSON.stringify({{
  commands: [...commands.keys()],
  hooks: [...hooks.keys()],
}}));
""")
    assert "adopt" in fired["commands"]
    assert "skill_changed" in fired["hooks"]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_adopt_command_handler_invokes_auto_pass(tmp_path: Path):
    config = a_config(tmp_path, '{"ok": true, "lines": ["Everything is aligned."]}')
    fired = run_node(tmp_path, f"""
const adopt = await import({adopt_uri()});
const commands = new Map();
adopt.registerAdopt({{
  registerCommand: (spec) => commands.set(spec.name, spec),
  on: () => {{}},
}}, {{ configPath: {json.dumps(str(config))} }});

const res = await commands.get("adopt").handler();
console.log(JSON.stringify({{ res }}));
""")
    assert fired["res"]["text"] == "Everything is aligned."


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_handle_skill_changed_invokes_engine(tmp_path: Path):
    config = a_config(tmp_path, '{"ok": true, "lines": ["Adopted safe skill."]}')
    fired = run_node(tmp_path, f"""
const adopt = await import({adopt_uri()});
let logged = [];
const logger = {{
  info: (msg) => logged.push(msg),
}};
await adopt.handleSkillChanged({{ action: "created" }}, {{
  configPath: {json.dumps(str(config))},
  logger,
}});
console.log(JSON.stringify({{ logged }}));
""")
    assert any("Adopted safe skill" in item for item in fired["logged"])


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_failed_scheduled_pass_is_a_log_line_and_never_a_crash(tmp_path: Path):
    """The tick has no chat to answer: `engine-run.mjs`'s sentence goes to the log."""
    fired = run_node(tmp_path, f"""
const adopt = await import({adopt_uri()});
let logged = [];
const logger = {{ info: (msg) => logged.push(msg) }};
await adopt.handleSkillChanged({{ action: "scheduled" }}, {{
  configPath: {json.dumps(str(tmp_path / "no-config-here.json"))},
  logger,
  reportFailures: true,
}});
console.log(JSON.stringify({{ logged }}));
""")
    assert any("engine path is not configured" in item for item in fired["logged"])


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_gateway_clock_starts_one_timer_and_clears_it_on_stop(tmp_path: Path):
    """`gateway_start` arms one interval, a second start does not arm another, `gateway_stop` clears it."""
    fired = run_node(tmp_path, f"""
const adopt = await import({adopt_uri()});
const hooks = new Map();
const api = {{ on: (name, fn) => {{
  if (!hooks.has(name)) hooks.set(name, []);
  hooks.get(name).push(fn);
}} }};
const armed = [];
const cleared = [];
const halfHour = 30 * 60 * 1000;
adopt.registerAdopt(api, {{
  intervalMs: halfHour,
  setIntervalFn: (fn, ms) => {{ armed.push(ms); return {{ id: armed.length }}; }},
  clearIntervalFn: (token) => cleared.push(token.id),
}});
const start = () => hooks.get("gateway_start").forEach((fn) => fn());
start();
start();
hooks.get("gateway_stop").forEach((fn) => fn());
console.log(JSON.stringify({{ armed, cleared, interval: adopt.ADOPT_INTERVAL_MS }}));
""")
    assert fired["armed"] == [30 * 60 * 1000]
    assert fired["cleared"] == [1]
    assert fired["interval"] == 30 * 60 * 1000


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_tick_of_the_gateway_clock_runs_the_pass(tmp_path: Path):
    config = a_config(tmp_path, '{"ok": true, "lines": ["Retired 1 absent server."]}')
    fired = run_node(tmp_path, f"""
const adopt = await import({adopt_uri()});
const hooks = new Map();
const logged = [];
const logger = {{ info: (msg) => logged.push(msg) }};
const api = {{
  on: (name, fn) => {{
    if (!hooks.has(name)) hooks.set(name, []);
    hooks.get(name).push(fn);
  }},
  logger,
}};
let tick = null;
adopt.registerAdopt(api, {{
  configPath: {json.dumps(str(config))},
  setIntervalFn: (fn) => {{ tick = fn; return {{ unref: () => {{}} }}; }},
}});
hooks.get("gateway_start").forEach((fn) => fn());
tick();
await new Promise((resolve) => setTimeout(resolve, 400));
console.log(JSON.stringify({{ logged }}));
""")
    assert any("Retired 1 absent server" in item for item in fired["logged"])
