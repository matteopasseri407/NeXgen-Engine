"""The one way the plugin starts the engine: no shell, a deadline, a sentence.

Every job that talks to the engine (`/quota`, `/council`, `/sup`) comes through
`engine-run.mjs`. These tests drive that module with node and a fake `spawn`, so
nothing here launches a real `nexgen`, and one test launches a real node child on
purpose: the detached run has to prove that what it prints lands in the log a
person is told to read.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from conftest import REAL_UL

PLUGIN = REAL_UL / "hooks" / "openclaw-guardrail-plugin"
NODE = shutil.which("node")

#: A `spawn` that records the call and answers like a child that never started.
RECORDER = """
const calls = [];
function recorder(argv, args, options) {
  calls.push({argv, args, options});
  throw new Error("the recorder does not start anything");
}
const child = {
  pid: 4242,
  stdout: {on: () => {}},
  stderr: {on: () => {}},
  on: () => {},
  unref: () => {},
};
function fakeSpawn(argv, args, options) {
  calls.push({argv, args, options});
  const listeners = {};
  child.on = (name, fn) => { listeners[name] = fn; };
  return child;
}
"""


def write_driver(tmp_path: Path, script: str) -> Path:
    driver = tmp_path / "driver.mjs"
    driver.write_text(script, encoding="utf-8")
    return driver


def run_driver(tmp_path: Path, script: str):
    done = subprocess.run(
        [NODE, str(write_driver(tmp_path, script))],
        capture_output=True, text=True, encoding="utf-8", timeout=90, check=False,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def engine_uri() -> str:
    return json.dumps((PLUGIN / "engine-run.mjs").as_uri())


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_run_without_a_shell_and_with_the_arguments_it_was_given(tmp_path):
    fired = run_driver(tmp_path, f"""
import * as engine from {engine_uri()};
{RECORDER}
let seen = null;
fakeSpawn = ((inner) => (argv, args, options) => {{
  seen = {{argv, args, options}};
  return inner(argv, args, options);
}})(fakeSpawn);
const config = {{engine: {{command: "/usr/bin/python3", entry: "/engine/cli/__init__.py"}}}};
const fs = await import("node:fs");
fs.writeFileSync({json.dumps(str(tmp_path / "nexgen.config.json"))}, JSON.stringify(config));
const answer = engine.runEngine(["quota", "--json"], {{
  configPath: {json.dumps(str(tmp_path / "nexgen.config.json"))},
  spawnFn: (argv, args, options) => {{ seen = {{argv, args, options}}; throw new Error("stop"); }},
  label: "quota",
}});
console.log(JSON.stringify({{seen, answer: await answer}}));
""")

    assert fired["seen"]["argv"] == "/usr/bin/python3"
    assert fired["seen"]["args"] == ["/engine/cli/__init__.py", "quota", "--json"]
    assert fired["seen"]["options"]["shell"] is False
    assert fired["seen"]["options"]["windowsHide"] is True
    assert fired["answer"]["ok"] is False  # a child that cannot start is a sentence, not a throw


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_every_job_reads_its_sentences_from_the_same_place(tmp_path):
    """The label names the job and the hint names the command: nothing else may differ."""
    config = tmp_path / "nexgen.config.json"
    fired = run_driver(tmp_path, f"""
import * as engine from {engine_uri()};
const out = {{}};
for (const [label, hint] of [["quota", undefined], ["council", undefined], ["sup", "nexgen local sup status"]]) {{
  out[label] = await engine.engineLines(["--version"], {{
    configPath: {json.dumps(str(config))}, label, hint, spawnFn: () => {{ throw new Error("no"); }},
  }});
}}
console.log(JSON.stringify(out));
""")

    assert fired["quota"] == "quota: the engine path is not configured yet. Run `nexgen sync` and try again."
    assert fired["council"].startswith("council: the engine path is not configured yet.")
    # The hint is what a person types, so it is the one part a job may say differently.
    slow = run_driver(tmp_path, f"""
import * as engine from {engine_uri()};
console.log(JSON.stringify(engine.sentencesFor("sup", "nexgen local sup status")));
""")
    assert slow["tooSlow"] == "sup: the engine did not answer in time. Run `nexgen local sup status` in a terminal."
    assert slow["noStart"] == "sup: the engine could not be started. Run `nexgen local sup status` in a terminal."


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_lines_the_engine_printed_are_the_answer(tmp_path):
    script = tmp_path / "fake-engine.mjs"
    script.write_text(
        "console.log(JSON.stringify({lines: ['compito A: 2 unità', 'spesa: 0.10 $']}));",
        encoding="utf-8",
    )
    config = tmp_path / "nexgen.config.json"
    config.write_text(json.dumps({"engine": {"command": NODE, "entry": str(script)}}), encoding="utf-8")

    fired = run_driver(tmp_path, f"""
import * as engine from {engine_uri()};
const text = await engine.engineLines(["local", "sup", "status", "--json"], {{
  configPath: {json.dumps(str(config))}, label: "sup", hint: "nexgen local sup status",
}});
console.log(JSON.stringify({{text}}));
""")

    assert fired["text"] == "compito A: 2 unità\nspesa: 0.10 $"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_an_engine_that_fails_is_a_sentence_and_never_a_stack_trace(tmp_path):
    script = tmp_path / "fake-engine.mjs"
    script.write_text('console.error("Traceback (most recent call last):"); process.exit(3);', encoding="utf-8")
    config = tmp_path / "nexgen.config.json"
    config.write_text(json.dumps({"engine": {"command": NODE, "entry": str(script)}}), encoding="utf-8")

    fired = run_driver(tmp_path, f"""
import * as engine from {engine_uri()};
const text = await engine.engineLines(["local", "sup", "status"], {{
  configPath: {json.dumps(str(config))}, label: "sup", hint: "nexgen local sup status",
}});
console.log(JSON.stringify({{text}}));
""")

    assert fired["text"] == "sup: the engine answered with an error. Run `nexgen local sup status` in a terminal."


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_detached_run_writes_what_the_engine_printed_to_its_log(tmp_path):
    """The run outlives the turn: its output has to exist somewhere a person can read it."""
    script = tmp_path / "fake-engine.mjs"
    script.write_text('console.log("[council] final verdict: APPROVE");', encoding="utf-8")
    config = tmp_path / "nexgen.config.json"
    config.write_text(json.dumps({"engine": {"command": NODE, "entry": str(script)}}), encoding="utf-8")
    log_dir = tmp_path / "logs"

    fired = run_driver(tmp_path, f"""
import * as engine from {engine_uri()};
const started = engine.startEngine(["council", "brainstorm", "x"], {{
  configPath: {json.dumps(str(config))}, logDir: {json.dumps(str(log_dir))},
  logName: "council-abc.log", label: "council",
}});
console.log(JSON.stringify(started));
""")

    assert fired["ok"] is True, fired
    assert fired["pid"] > 0
    log = Path(fired["log"])
    assert log == log_dir / "council-abc.log"
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if "final verdict: APPROVE" in log.read_text(encoding="utf-8"):
            break
        time.sleep(0.1)
    assert "final verdict: APPROVE" in log.read_text(encoding="utf-8")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_detached_run_with_no_engine_configured_is_a_sentence(tmp_path):
    fired = run_driver(tmp_path, f"""
import * as engine from {engine_uri()};
console.log(JSON.stringify(engine.startEngine(["council", "x"], {{
  configPath: {json.dumps(str(tmp_path / "absent.json"))}, logDir: {json.dumps(str(tmp_path / "logs"))},
  label: "council",
}})));
""")

    assert fired["ok"] is False
    assert fired["text"].startswith("council: the engine path is not configured yet")
