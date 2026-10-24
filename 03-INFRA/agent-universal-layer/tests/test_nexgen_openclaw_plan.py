"""The plan: it says what would change, and it does not change anything.

`nexgen openclaw plan` is the read-only verb of this unit, so the test that
matters most here is the boring one: after a plan, the file in force has the
same bytes, the same timestamp, and the home around it has no new file.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import i18n  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.companion import Companion  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.detect import Detection, Installation, LocalModelInfo, LocalModels, Login  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.plan import build_plan, render_plan  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.profile import Choices  # noqa: E402 - sys.path shim for cloned checkout


@pytest.fixture(autouse=True)
def _english():
    i18n.set_language("en")
    yield
    i18n.set_language(None)


def detected(config_path: Path, *, installed: bool = True, companion: Companion | None = None) -> Detection:
    return Detection(
        openclaw=Installation(installed=installed, version="2026.10.6" if installed else None,
                              config_path=str(config_path), config_present=True),
        claude=Login(binary=True, login=True),
        codex=Login(binary=False),
        opencode_go=True,
        local=LocalModels(ollama=(LocalModelInfo(name="qwen3:8b:latest", context_window=8192),)),
        n8n=True,
        companion=companion or Companion(),
    )


def put_config(home: Path, config: dict) -> Path:
    path = home / ".openclaw" / "openclaw.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return path


def test_a_plan_leaves_the_file_and_the_home_exactly_as_they_were(tmp_path):
    home = tmp_path / "home"
    path = put_config(home, {"mcp": {"servers": {"x": {}}}, "channels": ["telegram"]})
    before = path.read_bytes()
    stamp = path.stat().st_mtime_ns
    before_tree = sorted(str(p) for p in home.rglob("*"))

    plan = build_plan(detected=detected(path), home=home, env={})

    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == stamp
    assert sorted(str(p) for p in home.rglob("*")) == before_tree
    assert plan.config["gateway"]["auth"]["token"] == "generated-by-nexgen-setup"


def test_the_plan_separates_what_it_adds_changes_and_leaves_alone(tmp_path):
    home = tmp_path / "home"
    current = {
        "mcp": {"servers": {"vault-library": {}}},
        "channels": ["telegram"],
        "plugins": {"allow": ["browser"], "entries": {}},
        "gateway": {"mode": "local", "port": 18789, "bind": "lan",
                    "auth": {"mode": "token", "token": "old"}, "tailscale": {"mode": "off"}},
        "agents": {"ownership": "explicit", "defaults": {"heartbeat": {"every": "5m"}}, "entries": {}},
        "tools": {"exec": {"security": "allowlist", "ask": "on-miss"}},
    }
    path = put_config(home, current)
    plan = build_plan(detected=detected(path), home=home, env={})

    assert "plugins.allow" in plan.changed
    assert "gateway.bind" in plan.changed
    assert "agents.defaults.heartbeat.every" in plan.changed
    assert "plugins.entries.anthropic.config.sessionCatalog.enabled" in plan.added
    # The token is the person's: kept, and never offered as a change.
    assert plan.config["gateway"]["auth"]["token"] == "old"
    assert "gateway.auth.token" not in plan.changed
    assert plan.kept == ("channels", "mcp")
    assert "google" not in json.dumps(plan.to_dict()["config"]).lower()


def test_a_key_the_engine_no_longer_proposes_is_named_not_deleted(tmp_path):
    home = tmp_path / "home"
    current = {
        "plugins": {"allow": ["browser"], "entries": {"anthropic": {"config": {"sessionCatalog": {"enabled": True}}}}},
        "models": {"providers": {"ollama": {"baseUrl": "http://old", "api": "ollama", "models": []}}},
        "agents": {"ownership": "explicit", "defaults": {"heartbeat": {"every": "0m"}}, "entries": {}},
        "tools": {"exec": {"security": "allowlist", "ask": "on-miss"}},
        "gateway": {"mode": "local", "port": 18789, "bind": "loopback",
                    "auth": {"mode": "token", "token": "generated-by-nexgen-setup"},
                    "tailscale": {"mode": "off"}},
    }
    path = put_config(home, current)
    # A machine with the Go key only: no Claude subscription, no local models.
    machine = Detection(
        openclaw=Installation(installed=True, version="2026.10.6", config_path=str(path), config_present=True),
        claude=Login(binary=False),
        codex=Login(binary=False),
        opencode_go=True,
        local=LocalModels(),
        n8n=False,
    )
    plan = build_plan(detected=machine, home=home, env={})
    assert "plugins.entries.anthropic.config.sessionCatalog.enabled" in plan.dropped
    assert "models.providers.ollama.baseUrl" in plan.dropped
    assert path.exists()


def test_an_absent_file_is_a_complete_creation_not_an_error(tmp_path):
    home = tmp_path / "home"
    plan = build_plan(detected=detected(home / ".openclaw" / "openclaw.json"), home=home, env={})
    assert plan.current is None
    assert plan.current_error is None
    assert plan.changed == ()
    assert "plugins.allow" in plan.added


def test_an_existing_workspace_is_kept_and_never_offered_as_a_change(tmp_path):
    home = tmp_path / "home"
    current = {"agents": {"entries": {"main": {"workspace": "D:\\keep\\me"}}}}
    path = put_config(home, current)
    plan = build_plan(detected=detected(path), home=home, env={})
    assert plan.config["agents"]["entries"]["main"]["workspace"] == "D:\\keep\\me"
    assert "agents.entries.main.workspace" not in plan.changed
    assert "agents.entries.main.workspace" not in plan.added


def test_the_plan_says_when_a_context_window_is_unknown(tmp_path):
    home = tmp_path / "home"
    path = put_config(home, {})
    machine = Detection(
        openclaw=Installation(installed=True, version="2026.10.6", config_path=str(path), config_present=True),
        claude=Login(binary=False),
        codex=Login(binary=False),
        opencode_go=False,
        local=LocalModels(llamacpp=(LocalModelInfo(name="local-model"),)),
        n8n=False,
    )
    plan = build_plan(detected=machine, home=home, env={})
    assert "context window" in " ".join(plan.notes).lower()
    assert "local-model" in " ".join(plan.notes)


def test_an_unreadable_file_is_reported_in_the_plan(tmp_path):
    home = tmp_path / "home"
    path = home / ".openclaw" / "openclaw.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    plan = build_plan(detected=detected(path), home=home, env={})
    assert plan.current is None
    assert "JSONDecodeError" in (plan.current_error or "")


def test_the_plan_is_json_and_names_its_agents(tmp_path):
    home = tmp_path / "home"
    path = put_config(home, {})
    plan = build_plan(detected=detected(path), home=home, env={}, choices=Choices(lan=True))
    payload = json.loads(json.dumps(plan.to_dict()))
    assert payload["agents"]["main"] == "opencode-go/deepseek-v4.1-flash"
    assert payload["agents"]["capo"] == "anthropic/claude-opus-5-5"
    assert payload["agents"]["automazioni"] == "opencode-go/deepseek-v4-pro"
    assert payload["config"]["gateway"]["bind"] == "lan"
    assert payload["current"]["valid"] is True
    assert payload["changes"]["kept"] == []


def test_the_human_plan_says_that_nothing_was_written(tmp_path):
    home = tmp_path / "home"
    path = put_config(home, {})
    text = render_plan(build_plan(detected=detected(path), home=home, env={}))
    assert "Nothing was written" in text
    assert "opencode-go/deepseek-v4.1-flash" in text
    assert "assistente" in text


# ------------------------------------------------------------- the Windows app


def app_gateway(port: int | None = None) -> Companion:
    """The app's own gateway, as detection finds it on a Windows machine."""
    return Companion(
        present=True, owns_local_gateway=True, port=port,
        signals=("the app-owned WSL distribution OpenClawGateway",),
    )


def test_the_plan_names_the_app_gateway_and_which_one_the_app_uses(tmp_path):
    """Case (c): the native setup still applies, and the plan says the app is here too."""
    home = tmp_path / "home"
    path = put_config(home, {})

    text = render_plan(build_plan(detected=detected(path, companion=app_gateway(port=18789)), home=home, env={}))

    assert "has a gateway of its own on this PC" in text
    assert "connected to this PC's gateway on port 18789" in text


def test_the_plan_says_when_the_app_owns_the_only_gateway(tmp_path):
    """Case (b): the plan says the setup stops, so it never promises a write it will not do."""
    home = tmp_path / "home"
    path = put_config(home, {})

    text = render_plan(build_plan(
        detected=detected(path, installed=False, companion=app_gateway()), home=home, env={},
    ))

    assert "stops here instead of installing a second one" in text
