"""The guardrail adapters are executed here, not just copied.

Everything else about the guardrail is tested through the Python side: that
the body is deployed, that the registration lands in the right file, that a
posture with no installable guardrail is refused. All of that can pass while
the adapter itself never denies anything, because those tests copy a stub.

That gap hid two real defects on 2026-07-31: the OpenCode plugin read its
sidecar once at load time and registered no handler at all when the file was
not there yet, and both adapters answered "allow" when the sidecar existed
but could not be parsed. Under a no-prompt posture the adapter is the only
brake left, so a brake that silently does nothing is worse than none: the
user believes they are covered.

These tests run the real files with node.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed on this machine")

# A guardrail body speaking the same stdin/stdout contract a Claude PreToolUse
# hook speaks: deny anything mentioning a recursive delete, allow the rest.
GUARDRAIL_BODY = """
const raw = require("node:fs").readFileSync(0, "utf8");
const input = JSON.parse(raw);
const command = (input.tool_input && input.tool_input.command) || "";
const dangerous = command.includes("rm -rf");
process.stdout.write(JSON.stringify({
  hookSpecificOutput: {
    hookEventName: "PreToolUse",
    permissionDecision: dangerous ? "deny" : "allow",
    permissionDecisionReason: dangerous ? "recursive delete" : "",
  },
}));
"""

CRASHING_BODY = "process.exit(9);\n"


def _install(tmp_path: Path, adapter: str, sidecar: object | None) -> Path:
    """Copy the real adapter into a scratch dir, next to an optional sidecar."""
    dst = tmp_path / adapter
    shutil.copy2(HOOKS_DIR / adapter, dst)
    if sidecar is not None:
        payload = sidecar if isinstance(sidecar, str) else json.dumps(sidecar)
        (tmp_path / "nexgen-guardrail.config.json").write_text(payload, encoding="utf-8")
    return dst


def _body(tmp_path: Path, source: str = GUARDRAIL_BODY, name: str = "body.cjs") -> str:
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return str(path)


# ── Antigravity: one process per command, stdin/stdout JSON ──────────────

def _run_antigravity(adapter: Path, command: str) -> dict:
    payload = json.dumps({
        "toolCall": {"name": "run_command", "args": {"CommandLine": command}},
        "workspacePaths": ["/tmp"],
        "conversationId": "test-conversation",
    })
    proc = subprocess.run(
        [NODE, str(adapter)], input=payload, capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_antigravity_adapter_denies_a_dangerous_command(tmp_path):
    adapter = _install(tmp_path, "antigravity-guardrail-adapter.mjs",
                       {"hooks": [{"file": _body(tmp_path), "timeout": 5}]})
    assert _run_antigravity(adapter, "rm -rf /")["decision"] == "deny"


def test_antigravity_adapter_allows_an_ordinary_command(tmp_path):
    adapter = _install(tmp_path, "antigravity-guardrail-adapter.mjs",
                       {"hooks": [{"file": _body(tmp_path), "timeout": 5}]})
    assert _run_antigravity(adapter, "ls -la")["decision"] == "allow"


def test_antigravity_adapter_allows_when_no_guardrail_is_configured(tmp_path):
    """No sidecar at all is the documented no-op contract, not a failure."""
    adapter = _install(tmp_path, "antigravity-guardrail-adapter.mjs", None)
    assert _run_antigravity(adapter, "rm -rf /")["decision"] == "allow"


@pytest.mark.parametrize("sidecar", ["{ this is not json", '{"hooks": "not a list"}'])
def test_antigravity_adapter_asks_when_the_sidecar_is_broken(tmp_path, sidecar):
    """A guardrail that is present and unusable must never read as absent."""
    adapter = _install(tmp_path, "antigravity-guardrail-adapter.mjs", sidecar)
    result = _run_antigravity(adapter, "ls -la")
    assert result["decision"] == "ask", result
    assert "nexgen-guardrail" in result.get("reason", "")


def test_antigravity_adapter_asks_when_the_guardrail_body_crashes(tmp_path):
    adapter = _install(tmp_path, "antigravity-guardrail-adapter.mjs",
                       {"hooks": [{"file": _body(tmp_path, CRASHING_BODY), "timeout": 5}]})
    assert _run_antigravity(adapter, "ls -la")["decision"] == "ask"


# ── OpenCode: a plugin factory whose handler mutates output.status ───────

DRIVER = """
import plugin from "./opencode-guardrail-plugin.mjs";
const hooks = await plugin({ directory: "/tmp" });
const handler = hooks["permission.ask"];
const output = { status: "UNSET" };
if (typeof handler === "function") {
  await handler(
    { type: "bash", metadata: { command: process.argv[2] }, sessionID: "s" },
    output,
  );
}
process.stdout.write(JSON.stringify({
  registered: typeof handler === "function",
  status: output.status,
}));
"""


def _run_opencode(tmp_path: Path, command: str) -> dict:
    (tmp_path / "driver.mjs").write_text(DRIVER, encoding="utf-8")
    proc = subprocess.run(
        [NODE, str(tmp_path / "driver.mjs"), command],
        capture_output=True, text=True, timeout=30, cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_opencode_plugin_denies_a_dangerous_command(tmp_path):
    _install(tmp_path, "opencode-guardrail-plugin.mjs",
             {"hooks": [{"file": _body(tmp_path), "timeout": 5}]})
    assert _run_opencode(tmp_path, "rm -rf /")["status"] == "deny"


def test_opencode_plugin_leaves_an_ordinary_command_alone(tmp_path):
    _install(tmp_path, "opencode-guardrail-plugin.mjs",
             {"hooks": [{"file": _body(tmp_path), "timeout": 5}]})
    assert _run_opencode(tmp_path, "ls -la")["status"] == "UNSET"


def test_opencode_plugin_registers_its_handler_before_any_sidecar_exists(tmp_path):
    """OpenCode calls the factory once, when the plugin loads.

    A session started before agent-sync wrote the sidecar used to get no
    permission handler for its entire life, with the no-prompt posture
    already on disk. The handler must exist regardless, and pick the
    configuration up on the next command."""
    _install(tmp_path, "opencode-guardrail-plugin.mjs", None)
    assert _run_opencode(tmp_path, "rm -rf /")["registered"] is True

    # The sidecar lands later, mid-session: the very next command sees it.
    (tmp_path / "nexgen-guardrail.config.json").write_text(
        json.dumps({"hooks": [{"file": _body(tmp_path), "timeout": 5}]}), encoding="utf-8"
    )
    assert _run_opencode(tmp_path, "rm -rf /")["status"] == "deny"


@pytest.mark.parametrize("sidecar", ["{ this is not json", '{"hooks": "not a list"}'])
def test_opencode_plugin_asks_when_the_sidecar_is_broken(tmp_path, sidecar):
    _install(tmp_path, "opencode-guardrail-plugin.mjs", sidecar)
    assert _run_opencode(tmp_path, "ls -la")["status"] == "ask"


def test_opencode_plugin_asks_when_the_guardrail_body_crashes(tmp_path):
    _install(tmp_path, "opencode-guardrail-plugin.mjs",
             {"hooks": [{"file": _body(tmp_path, CRASHING_BODY), "timeout": 5}]})
    assert _run_opencode(tmp_path, "ls -la")["status"] == "ask"
