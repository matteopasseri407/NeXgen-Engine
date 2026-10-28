"""A second setup on a machine already configured writes nothing.

The engine rewrites `openclaw.json` from a proposal merged over the file in
force, so idempotence is not a cosmetic property: a proposal that drifts by one
key on every run means the file is rewritten at every setup, the gateway reloads
for nothing, and the person reading `nexgen openclaw plan` is told there are
changes where there are none.

The file below is the shape of a real node's configuration, with the gateway
token replaced by a placeholder and the tailnet name by an example: the sections
the engine does not own (channels, memory, nodeHost, session, meta, wizard) are
there on purpose, because those are what a merge can break first.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import i18n  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw import setup_write  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.detect import Detection, Installation, LocalModelInfo, LocalModels, Login  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.plan import build_plan  # noqa: E402 - sys.path shim for cloned checkout


@pytest.fixture(autouse=True)
def _english():
    i18n.set_language("en")
    yield
    i18n.set_language(None)


def configured_node() -> dict:
    """A working node: a gateway elsewhere, its own deny list, a plugin allowlist, skills decided."""
    return {
        "meta": {"lastTouchedVersion": "2026.8.1"},
        "gateway": {
            "mode": "remote",
            "port": 18789,
            "remote": {"url": "wss://gateway.example.ts.net"},
            "tailscale": {"mode": "off"},
        },
        "agents": {
            "entries": {
                "main": {
                    "name": "main",
                    "identity": {"name": "assistente"},
                    "workspace": "/home/person/.openclaw/workspace",
                    "model": "opencode-go/deepseek-v4.1-flash",
                    "tools": {"agentToAgent": {"send": ["capo"]}},
                },
                "capo": {
                    "name": "capo",
                    "identity": {"name": "capo"},
                    "workspace": "/home/person/.openclaw/workspace-capo",
                    "model": {"primary": "anthropic/claude-opus-5-5", "fallbacks": ["opencode-go/deepseek-v4-pro"]},
                },
            },
        },
        "tools": {
            "exec": {"mode": "ask"},
            "deny": ["edit", "firecrawl__firecrawl_scrape"],
            "byProvider": {
                "ollama/qwen3:8b": {"profile": "messaging", "alsoAllow": ["read"], "deny": ["group:web"]},
            },
            "agentToAgent": {"enabled": True, "allow": ["capo", "main"]},
        },
        "plugins": {
            "allow": ["acpx", "ollama", "telegram"],
            "entries": {"ollama": {"enabled": True}, "acpx": {"enabled": True}},
        },
        "skills": {
            "allowBundled": [],
            "entries": {"humanizer": {"enabled": True}, "impeccable": {"enabled": False}},
        },
        "channels": {"telegram": {"enabled": True}},
        "bindings": [{"agentId": "main", "match": {"channel": "telegram", "accountId": "*"}}],
        "mcp": {"servers": {"vault-library": {"command": "node", "args": ["bridge.mjs", "${VAULT_URL}"]}}},
        "session": {"store": "sqlite", "maintenance": {"mode": "enforce"}},
        "memory": {"enabled": True},
        "nodeHost": {"workerRuns": {"enabled": True}},
        "update": {"checkOnStart": False, "auto": {"enabled": False}},
        "wizard": {"lastRunVersion": "2026.8.1"},
    }


def detected(config_path: Path) -> Detection:
    return Detection(
        openclaw=Installation(installed=True, version="2026.10.6", config_path=str(config_path), config_present=True),
        claude=Login(binary=True, login=True),
        codex=Login(binary=True, login=True),
        opencode_go=True,
        local=LocalModels(ollama=(LocalModelInfo(name="qwen3:8b", context_window=32768),)),
        n8n=True,
    )


def write(home: Path, document: dict) -> Path:
    path = home / ".openclaw" / "openclaw.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(setup_write.render(document), encoding="utf-8")
    return path


def one_setup(home: Path) -> tuple[dict, str]:
    """What `nexgen openclaw setup` writes, and the plan it was built from."""
    path = home / ".openclaw" / "openclaw.json"
    plan = build_plan(detected=detected(path), home=home, env={}, plugin_deposited=True)
    merged, _generated = setup_write.with_token(setup_write.merged_document(plan.current, plan.config))
    text = setup_write.render(merged)
    path.write_text(text, encoding="utf-8")
    return plan.config, text


def test_a_second_setup_on_a_configured_machine_writes_nothing(tmp_path):
    """The condition `write_step` uses: the document equals the file it was built from.

    `plan.added` is not the measure here. It names what the proposal carries that
    the file does not, and the merge is what decides whether a leaf moves: a
    model written as a string is completed with its fallback chain by the merge,
    so the proposal always looks one key ahead of the file. What must not happen
    is a write, and `write_step` compares the merged document with the file.
    """
    home = tmp_path / "home"
    write(home, configured_node())

    _config, first = one_setup(home)
    plan = build_plan(detected=detected(home / ".openclaw" / "openclaw.json"), home=home, env={},
                      plugin_deposited=True)
    merged, _generated = setup_write.with_token(setup_write.merged_document(plan.current, plan.config))

    assert merged == plan.current, "the setup would see the file as already written"
    assert setup_write.render(merged) == first, "the file after the second run is the file after the first"


def test_the_setup_keeps_the_decisions_the_machine_already_made(tmp_path):
    """The three that used to be overwritten: exec mode, gateway role, a skill's toggle."""
    home = tmp_path / "home"
    before = configured_node()
    write(home, before)

    one_setup(home)

    after = json.loads((home / ".openclaw" / "openclaw.json").read_text(encoding="utf-8"))
    assert after["tools"]["exec"] == {"mode": "ask"}
    assert after["gateway"]["mode"] == "remote"
    assert "auth" not in after["gateway"], "a node was not given a local token"
    assert after["gateway"]["remote"] == {"url": "wss://gateway.example.ts.net"}
    assert after["skills"]["entries"]["humanizer"] == {"enabled": True}
    assert after["tools"]["deny"] == ["edit", "firecrawl__firecrawl_scrape"], "the person's deny list stays"


def test_the_setup_does_not_touch_what_the_engine_does_not_own(tmp_path):
    home = tmp_path / "home"
    original = copy.deepcopy(configured_node())
    write(home, original)

    one_setup(home)

    after = json.loads((home / ".openclaw" / "openclaw.json").read_text(encoding="utf-8"))
    for section in ("channels", "memory", "nodeHost", "session", "mcp", "wizard", "meta"):
        assert after[section] == original[section], section
