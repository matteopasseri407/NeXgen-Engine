"""Session continuity between OpenClaw and Claude Code via machine-local state.

These tests prove that:
1. `after_compaction` and `session_end` write continuity checkpoints to `openclaw-checkpoint.json`
   in the machine-local state directory when `checkpoint.enabled` is true.
2. The checkpoint record captures origin (`openclaw`), agent, session_key in the shape
   accepted by `openclaw mcp serve`, event, reason, and timestamp.
3. No hook ever writes directly into the KnowledgeVault (the vault remains untouched).
4. When `checkpoint.enabled` is false or missing, no checkpoint file is written.
5. Any filesystem error during write is swallowed and never escapes to OpenClaw.
6. Claude's checkpoint hook reads recent checkpoints (< 12 hours) from state and injects
   guidance into context, while remaining strictly read-only ("never writes").
7. When the checkpoint is older than 12 hours or absent, Claude's hook injects only the briefing.
8. `nexgen openclaw doctor` reports `openclaw.checkpoint` as PASS when enabled and WARN when disabled.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest
from conftest import REAL_UL
from nexgen_core.openclaw.detect import Detection, Installation, LocalModels, Login
from nexgen_core.openclaw.doctor import collect
from nexgen_core.report import Severity

HOOKS = REAL_UL / "hooks"
PLUGIN_SOURCE = HOOKS / "openclaw-guardrail-plugin"
CLAUDE_HOOK = HOOKS / "claude-vault-checkpoint.mjs"
BRIEFING_TEXT = (HOOKS / "vault-briefing.txt").read_text(encoding="utf-8").strip()
NODE = shutil.which("node")

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
"""


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    env = dict(os.environ)
    env.update(HOME=str(tmp_path / "home"), USERPROFILE=str(tmp_path / "home"), **extra)
    return env


def _deploy_plugin(tmp_path: Path, config: dict | None = None) -> Path:
    target = tmp_path / "plugin"
    target.mkdir(parents=True, exist_ok=True)
    for source in sorted(PLUGIN_SOURCE.iterdir()):
        if source.is_file():
            shutil.copy2(source, target / source.name)
    shutil.copy2(HOOKS / "nexgen-guardrail-core.mjs", target / "nexgen-guardrail-core.mjs")
    shutil.copy2(HOOKS / "vault-briefing.txt", target / "vault-briefing.txt")
    if config is not None:
        (target / "nexgen.config.json").write_text(json.dumps(config), encoding="utf-8")
    return target


def _run_node_driver(plugin_dir: Path, script: str, env: dict[str, str]) -> str:
    driver = plugin_dir / "driver.mjs"
    driver.write_text(
        PREAMBLE.format(uri=json.dumps((plugin_dir / "index.mjs").as_uri())) + script,
        encoding="utf-8",
    )
    done = subprocess.run(
        [NODE, str(driver)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env=env,
    )
    assert done.returncode == 0, f"Driver failed:\nstdout: {done.stdout}\nstderr: {done.stderr}"
    return done.stdout.strip()


def _machine_detection(config_path: Path) -> Detection:
    return Detection(
        openclaw=Installation(
            installed=True, version="2026.10.6", config_path=str(config_path), config_present=True,
        ),
        claude=Login(binary=False, login=None),
        codex=Login(binary=False, login=None),
        opencode_go=True,
        local=LocalModels(ollama=()),
        n8n=False,
    )


# ---------------------------------------------------------------- plugin hooks


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_compaction_writes_checkpoint_to_state_dir_and_never_touches_vault(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    plugin = _deploy_plugin(tmp_path, config={
        "checkpoint": {"enabled": True, "state_dir": str(state / "nexgen")},
    })

    script = """
await call("after_compaction", { compactedCount: 8, reason: "compaction" }, { sessionId: "sess-100", agentId: "main" });
console.log(JSON.stringify({ ok: true }));
"""
    env = _env(tmp_path, AGENT_STATE_DIR=str(state), AGENT_VAULT_DATA=str(vault))
    out = _run_node_driver(plugin, script, env)
    assert json.loads(out)["ok"] is True

    checkpoint_file = state / "nexgen" / "openclaw-checkpoint.json"
    assert checkpoint_file.is_file()
    data = json.loads(checkpoint_file.read_text(encoding="utf-8"))
    assert data["origin"] == "openclaw"
    assert data["agent"] == "main"
    assert data["session_key"] == "sess-100"
    assert data["event"] == "after_compaction"
    assert data["reason"] == "compaction"
    assert isinstance(data["timestamp"], str) and len(data["timestamp"]) > 10

    # The vault must NEVER be touched by the hook
    assert not (vault / "04-NOW").exists()
    assert not list(vault.rglob("*"))


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_session_end_writes_checkpoint_to_state_dir_and_never_touches_vault(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    plugin = _deploy_plugin(tmp_path, config={
        "checkpoint": {"enabled": True, "state_dir": str(state / "nexgen")},
    })

    script = """
await call("session_end", { sessionKey: "agent:main:channel:whatsapp:user123", reason: "reset" }, { agentId: "assistant" });
console.log(JSON.stringify({ ok: true }));
"""
    env = _env(tmp_path, AGENT_STATE_DIR=str(state), AGENT_VAULT_DATA=str(vault))
    out = _run_node_driver(plugin, script, env)
    assert json.loads(out)["ok"] is True

    checkpoint_file = state / "nexgen" / "openclaw-checkpoint.json"
    assert checkpoint_file.is_file()
    data = json.loads(checkpoint_file.read_text(encoding="utf-8"))
    assert data["origin"] == "openclaw"
    assert data["agent"] == "assistant"
    assert data["session_key"] == "agent:main:channel:whatsapp:user123"
    assert data["event"] == "session_end"
    assert data["reason"] == "reset"
    assert isinstance(data["timestamp"], str)

    # The vault must NEVER be touched by the hook
    assert not (vault / "04-NOW").exists()
    assert not list(vault.rglob("*"))


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_disabled_switch_prevents_checkpoint_write(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    plugin = _deploy_plugin(tmp_path, config={
        "checkpoint": {"enabled": False, "state_dir": str(state / "nexgen")},
    })

    script = """
await call("after_compaction", { compactedCount: 2 }, { sessionId: "sess-off" });
await call("session_end", { sessionId: "sess-off", reason: "idle" }, {});
console.log(JSON.stringify({ ok: true }));
"""
    env = _env(tmp_path, AGENT_STATE_DIR=str(state))
    _run_node_driver(plugin, script, env)

    checkpoint_file = state / "nexgen" / "openclaw-checkpoint.json"
    assert not checkpoint_file.exists()


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_missing_switch_prevents_checkpoint_write(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    plugin = _deploy_plugin(tmp_path, config={})

    script = """
await call("after_compaction", { compactedCount: 2 }, { sessionId: "sess-missing" });
console.log(JSON.stringify({ ok: true }));
"""
    env = _env(tmp_path, AGENT_STATE_DIR=str(state))
    _run_node_driver(plugin, script, env)

    checkpoint_file = state / "nexgen" / "openclaw-checkpoint.json"
    assert not checkpoint_file.exists()


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_write_failure_is_swallowed_without_throwing_to_openclaw(tmp_path: Path) -> None:
    # A path that points to a file prevents directory creation
    bad_state = tmp_path / "file-not-dir"
    bad_state.write_text("blocked", encoding="utf-8")
    plugin = _deploy_plugin(tmp_path, config={
        "checkpoint": {"enabled": True, "state_dir": str(bad_state / "nexgen")},
    })

    script = """
let threw = false;
try {
  await call("after_compaction", { compactedCount: 1 }, { sessionId: "sess-err" });
  await call("session_end", { sessionId: "sess-err" }, {});
} catch (e) {
  threw = true;
}
console.log(JSON.stringify({ threw }));
"""
    env = _env(tmp_path, AGENT_STATE_DIR=str(bad_state))
    out = _run_node_driver(plugin, script, env)
    assert json.loads(out)["threw"] is False


# ------------------------------------------------ claude hook continuity


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_claude_checkpoint_hook_injects_note_when_checkpoint_is_recent(tmp_path: Path) -> None:
    state = tmp_path / "state" / "nexgen"
    state.mkdir(parents=True)
    checkpoint_file = state / "openclaw-checkpoint.json"
    now_iso = datetime.now(timezone.utc).isoformat()
    checkpoint_file.write_text(json.dumps({
        "origin": "openclaw",
        "agent": "developer",
        "session_key": "agent:developer:channel:discord:ch1",
        "event": "after_compaction",
        "reason": "compaction",
        "timestamp": now_iso,
    }), encoding="utf-8")

    event = json.dumps({"hook_event_name": "SessionStart", "source": "compact"})
    env = _env(tmp_path, AGENT_STATE_DIR=str(tmp_path / "state"))
    done = subprocess.run(
        [NODE, str(CLAUDE_HOOK)],
        input=event,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=30,
        check=False,
    )
    assert done.returncode == 0
    parsed = json.loads(done.stdout)
    context = parsed["hookSpecificOutput"]["additionalContext"]
    assert BRIEFING_TEXT in context
    assert "L'ultima sessione OpenClaw è dell'agente developer" in context
    assert "agent:developer:channel:discord:ch1" in context
    assert "openclaw" in context


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_claude_checkpoint_hook_omits_note_when_checkpoint_is_old(tmp_path: Path) -> None:
    state = tmp_path / "state" / "nexgen"
    state.mkdir(parents=True)
    checkpoint_file = state / "openclaw-checkpoint.json"
    # 24 hours ago (exceeds the 12-hour window)
    old_iso = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    checkpoint_file.write_text(json.dumps({
        "origin": "openclaw",
        "agent": "developer",
        "session_key": "sess-old",
        "timestamp": old_iso,
    }), encoding="utf-8")

    event = json.dumps({"hook_event_name": "SessionStart", "source": "compact"})
    env = _env(tmp_path, AGENT_STATE_DIR=str(tmp_path / "state"))
    done = subprocess.run(
        [NODE, str(CLAUDE_HOOK)],
        input=event,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=30,
        check=False,
    )
    assert done.returncode == 0
    parsed = json.loads(done.stdout)
    context = parsed["hookSpecificOutput"]["additionalContext"]
    assert context == BRIEFING_TEXT


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_claude_checkpoint_hook_omits_note_when_checkpoint_is_absent(tmp_path: Path) -> None:
    event = json.dumps({"hook_event_name": "SessionStart", "source": "compact"})
    env = _env(tmp_path, AGENT_STATE_DIR=str(tmp_path / "non-existent-state"))
    done = subprocess.run(
        [NODE, str(CLAUDE_HOOK)],
        input=event,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=30,
        check=False,
    )
    assert done.returncode == 0
    parsed = json.loads(done.stdout)
    context = parsed["hookSpecificOutput"]["additionalContext"]
    assert context == BRIEFING_TEXT


# ---------------------------------------------------------------- doctor check


def test_doctor_reports_checkpoint_pass_when_enabled(tmp_path: Path) -> None:
    home = tmp_path / "home"
    state = home / ".openclaw"
    state.mkdir(parents=True)
    config_path = state / "openclaw.json"
    config_path.write_text(json.dumps({"gateway": {"port": 18789}}), encoding="utf-8")
    plugin_ext = state / "extensions" / "nexgen"
    plugin_ext.mkdir(parents=True)
    (plugin_ext / "nexgen.config.json").write_text(
        json.dumps({"checkpoint": {"enabled": True}}), encoding="utf-8",
    )

    outcomes = collect(
        detected=_machine_detection(config_path),
        home=home,
        env=_env(tmp_path),
        connect=lambda h, p, t: False,
    )
    matches = [o for o in outcomes if o.id == "openclaw.checkpoint"]
    assert len(matches) == 1
    assert matches[0].severity == Severity.OK
    assert "abilitato" in matches[0].message or "enabled" in matches[0].message


def test_doctor_reports_checkpoint_warn_when_disabled(tmp_path: Path) -> None:
    home = tmp_path / "home"
    state = home / ".openclaw"
    state.mkdir(parents=True)
    config_path = state / "openclaw.json"
    config_path.write_text(json.dumps({"gateway": {"port": 18789}}), encoding="utf-8")
    plugin_ext = state / "extensions" / "nexgen"
    plugin_ext.mkdir(parents=True)
    (plugin_ext / "nexgen.config.json").write_text(
        json.dumps({"checkpoint": {"enabled": False}}), encoding="utf-8",
    )

    outcomes = collect(
        detected=_machine_detection(config_path),
        home=home,
        env=_env(tmp_path),
        connect=lambda h, p, t: False,
    )
    matches = [o for o in outcomes if o.id == "openclaw.checkpoint"]
    assert len(matches) == 1
    assert matches[0].severity == Severity.WARN
    assert "disabilitato" in matches[0].message or "disabled" in matches[0].message
