"""The governor job of the `nexgen` plugin, run the way OpenClaw runs it.

These tests load the real plugin with node, register it against a fake `api` that
records the hooks and the log lines, and fire `before_model_resolve` with the
context OpenClaw documents (`agentId`, `modelProviderId`, `modelId`). Nothing is
asserted from a reading of the source: every decision below is what the plugin
actually answered, and every state file is written under `tmp_path`.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from conftest import REAL_UL

HOOKS = REAL_UL / "hooks"
PLUGIN_SOURCE = HOOKS / "openclaw-guardrail-plugin"
NODE = shutil.which("node")

PREAMBLE = """
import plugin from {uri};
const handlers = new Map();
const commands = new Map();
const logs = [];
plugin.register({{
  on: (name, fn) => {{
    if (!handlers.has(name)) handlers.set(name, []);
    handlers.get(name).push(fn);
  }},
  registerCommand: (spec) => commands.set(spec.name, spec),
  logger: {{
    info: (message) => logs.push(message),
    warn: (message) => logs.push(message),
    error: (message) => logs.push(message),
  }},
}});
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
"""


def deployed(tmp_path: Path, *, governor: dict | None = None, state: dict | str | None = None,
             fresh: bool = True) -> Path:
    """The plugin as the guard deposits it, with the two sidecars the governor reads."""
    target = tmp_path / "deployed"
    target.mkdir()
    for source in sorted(PLUGIN_SOURCE.iterdir()):
        if source.is_file():
            shutil.copy2(source, target / source.name)
    for extra in ("nexgen-guardrail-core.mjs", "vault-briefing.txt"):
        if (HOOKS / extra).is_file():
            shutil.copy2(HOOKS / extra, target / extra)
    if governor is not None:
        (target / "nexgen.config.json").write_text(json.dumps({"governor": governor}), encoding="utf-8")
    if state is not None:
        text = state if isinstance(state, str) else json.dumps(state)
        (target / "nexgen-quota-state.json").write_text(text, encoding="utf-8")
    elif fresh:
        (target / "nexgen-quota-state.json").write_text(
            json.dumps({"written_at": time.time(), "providers": {}}), encoding="utf-8",
        )
    return target


def fire(target: Path, script: str) -> dict:
    """Runs `script` after the preamble, in a fresh node process, and returns its JSON."""
    driver = target / "driver.mjs"
    driver.write_text(PREAMBLE.format(uri=json.dumps((target / "index.mjs").as_uri())) + script,
                      encoding="utf-8")
    done = subprocess.run([NODE, str(driver)], capture_output=True, text=True, timeout=60, check=False)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def a_state(percent: float, *, age: float = 0.0, window: str = "5h", provider: str = "opencode-go",
            extra: dict | None = None) -> dict:
    """A state file as the beat writes it: one reading per provider, some seconds old."""
    providers = {provider: {"percent": percent, "window": window}}
    providers.update(extra or {})
    return {"written_at": time.time() - age, "providers": providers}


def a_governor(*chain: str, block_at: float = 95, agent: str = "main", **extra) -> dict:
    section = {"block_at": block_at, "state_max_age_seconds": 3 * 3600, "agents": {agent: list(chain)}}
    section.update(extra)
    return section


def ask(target: Path, ctx: dict | None = None) -> dict:
    """Calls `decision` directly, against the two sidecars this test deployed."""
    return fire(target, f"""
import {{ writeFileSync }} from "node:fs";
import {{ dirname, join }} from "node:path";
import {{ fileURLToPath }} from "node:url";
const DIR = dirname(fileURLToPath(import.meta.url));
const governor = await import({json.dumps((target / "governor.mjs").as_uri())});
const answer = governor.decision({{ ctx: {json.dumps(ctx or {})},
  configPath: join(DIR, "nexgen.config.json"), log: {{ info: (message) => logs.push(message) }} }});
console.log(JSON.stringify({{ answer: answer === undefined ? null : answer, logs }}));
""")


# ------------------------------------------------------------------- the wiring


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_plugin_registers_the_governor_hook(tmp_path):
    fired = fire(deployed(tmp_path), "console.log(JSON.stringify([...handlers.keys()]));\n")

    assert "before_model_resolve" in fired


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_spent_plan_moves_the_turn_and_says_why(tmp_path):
    target = deployed(tmp_path, governor=a_governor("anthropic/claude-opus-5-5", "openai/gpt-6-luna"),
                      state=a_state(97.0))

    fired = fire(target, """
console.log(JSON.stringify({
  answer: await call("before_model_resolve", {prompt: "ciao"}, {agentId: "main", modelProviderId: "opencode-go", modelId: "deepseek-v4.1-flash"}),
  logs,
}));
""")

    assert fired["answer"] == {"providerOverride": "anthropic", "modelOverride": "claude-opus-5-5"}
    assert len(fired["logs"]) == 1
    assert "opencode-go" in fired["logs"][0] and "97%" in fired["logs"][0]
    assert "5h" in fired["logs"][0] and "anthropic/claude-opus-5-5" in fired["logs"][0]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_hook_registration_is_the_one_openclaw_calls(tmp_path):
    """The handler answers `undefined` when it has nothing to say, so the turn keeps its model."""
    target = deployed(tmp_path, governor=a_governor("anthropic/claude-opus-5-5"), state=a_state(20.0))

    fired = fire(target, """
console.log(JSON.stringify(await call("before_model_resolve", {prompt: "ciao"},
  {agentId: "main", modelProviderId: "opencode-go"})));
""")

    assert fired is None


# ------------------------------------------------------------------ the decision


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_plan_under_the_line_leaves_the_turn_alone(tmp_path):
    fired = ask(deployed(tmp_path, governor=a_governor("anthropic/claude-opus-5-5"), state=a_state(60.0)),
                {"agentId": "main", "modelProviderId": "opencode-go"})

    assert fired["answer"] is None and fired["logs"] == []


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_plan_exactly_on_the_line_moves_the_turn(tmp_path):
    fired = ask(deployed(tmp_path, governor=a_governor("anthropic/claude-opus-5-5", block_at=95),
                         state=a_state(95.0)),
                {"agentId": "main", "modelProviderId": "opencode-go"})

    assert fired["answer"]["providerOverride"] == "anthropic"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_line_is_the_owners_and_not_the_engines(tmp_path):
    """At 82% a plan is fine for one owner and spent for another: the policy decides."""
    target = deployed(tmp_path, governor=a_governor("anthropic/claude-opus-5-5", block_at=80),
                      state=a_state(82.0))

    fired = ask(target, {"agentId": "main", "modelProviderId": "opencode-go"})

    assert fired["answer"] == {"providerOverride": "anthropic", "modelOverride": "claude-opus-5-5"}


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_no_policy_means_no_early_switch(tmp_path):
    fired = ask(deployed(tmp_path, state=a_state(99.0)), {"agentId": "main", "modelProviderId": "opencode-go"})

    assert fired["answer"] is None


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_an_agent_with_no_chain_is_left_alone(tmp_path):
    target = deployed(tmp_path, governor=a_governor("anthropic/claude-opus-5-5", agent="capo"),
                      state=a_state(99.0))

    assert ask(target, {"agentId": "main", "modelProviderId": "opencode-go"})["answer"] is None


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_each_agent_gets_its_own_chain(tmp_path):
    governor = {"block_at": 95, "state_max_age_seconds": 10800,
                "agents": {"main": ["anthropic/claude-opus-5-5"], "capo": ["openai/gpt-6-luna"]}}
    target = deployed(tmp_path, governor=governor, state=a_state(99.0))

    fired = ask(target, {"agentId": "capo", "modelProviderId": "opencode-go"})

    assert fired["answer"] == {"providerOverride": "openai", "modelOverride": "gpt-6-luna"}


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_turn_with_no_provider_is_not_judged(tmp_path):
    """Before model resolution the provider may be unknown: guessing one would move the wrong turn."""
    target = deployed(tmp_path, governor=a_governor("anthropic/claude-opus-5-5"), state=a_state(99.0))

    assert ask(target, {"agentId": "main"})["answer"] is None


# -------------------------------------------------------------------- the state


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_reading_older_than_the_policy_allows_is_not_trusted(tmp_path):
    fired = ask(deployed(tmp_path, governor=a_governor("anthropic/claude-opus-5-5"),
                         state=a_state(99.0, age=4 * 3600)),
                {"agentId": "main", "modelProviderId": "opencode-go"})

    assert fired["answer"] is None and fired["logs"] == []


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_missing_state_is_not_trusted(tmp_path):
    target = deployed(tmp_path, governor=a_governor("anthropic/claude-opus-5-5"), fresh=False)

    assert ask(target, {"agentId": "main", "modelProviderId": "opencode-go"})["answer"] is None


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_an_unreadable_state_is_not_trusted(tmp_path):
    target = deployed(tmp_path, governor=a_governor("anthropic/claude-opus-5-5"), state="{not json")

    assert ask(target, {"agentId": "main", "modelProviderId": "opencode-go"})["answer"] is None


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_state_from_a_clock_that_moved_backwards_is_still_read(tmp_path):
    """A reading from the future is the last thing the engine wrote: refusing it would silence a beat."""
    fired = ask(deployed(tmp_path, governor=a_governor("anthropic/claude-opus-5-5"),
                         state=a_state(99.0, age=-600)),
                {"agentId": "main", "modelProviderId": "opencode-go"})

    assert fired["answer"]["providerOverride"] == "anthropic"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_sidecar_written_by_an_older_engine_uses_the_shipped_line(tmp_path):
    """No numbers in the section: the plugin falls back to its own, and never to a guess."""
    target = deployed(tmp_path, governor={"agents": {"main": ["anthropic/claude-opus-5-5"]}},
                      state=a_state(96.0))

    fired = ask(target, {"agentId": "main", "modelProviderId": "opencode-go"})

    assert fired["answer"] == {"providerOverride": "anthropic", "modelOverride": "claude-opus-5-5"}


# ------------------------------------------------------------------ the choice


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_chain_that_stays_on_the_spent_plan_is_no_answer(tmp_path):
    target = deployed(tmp_path, governor=a_governor("opencode-go/deepseek-v4-pro"), state=a_state(99.0))

    assert ask(target, {"agentId": "main", "modelProviderId": "opencode-go"})["answer"] is None


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_first_fallback_that_still_has_room_wins(tmp_path):
    """A second plan can be spent too: the chain is walked until one answers."""
    state = a_state(99.0, extra={"anthropic": {"percent": 99.0, "window": "7d"}})
    target = deployed(tmp_path,
                      governor=a_governor("opencode-go/deepseek-v4-pro", "anthropic/claude-opus-5-5",
                                          "openai/gpt-6-luna"),
                      state=state)

    fired = ask(target, {"agentId": "main", "modelProviderId": "opencode-go"})

    assert fired["answer"] == {"providerOverride": "openai", "modelOverride": "gpt-6-luna"}
    assert "openai/gpt-6-luna" in fired["logs"][0]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_chain_entry_that_is_not_provider_slash_model_is_ignored(tmp_path):
    target = deployed(tmp_path, governor=a_governor("claude-opus", "anthropic/claude-opus-5-5"),
                      state=a_state(99.0))

    fired = ask(target, {"agentId": "main", "modelProviderId": "opencode-go"})

    assert fired["answer"] == {"providerOverride": "anthropic", "modelOverride": "claude-opus-5-5"}


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_an_openclaw_without_the_hook_surface_breaks_nothing(tmp_path):
    target = deployed(tmp_path, governor=a_governor("anthropic/claude-opus-5-5"))

    fired = fire(target, f"""
const governor = await import({json.dumps((target / "governor.mjs").as_uri())});
governor.registerGovernor({{}});
governor.registerGovernor({{ on: "not a function" }});
console.log(JSON.stringify({{ ok: true }}));
""")

    assert fired == {"ok": True}
