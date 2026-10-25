"""The doctor: a good configuration passes, a dangerous one is named.

The first test is the one that matters most: a machine that never chose
OpenClaw must not be told anything about it, because a doctor that invents work
is a doctor nobody reads.
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
from nexgen_core.openclaw.companion import Companion, probe  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.detect import Detection, Installation, LocalModelInfo, LocalModels, Login, detect  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.doctor import collect, exit_code  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.profile import PENDING_TOKEN  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.report import Severity  # noqa: E402 - sys.path shim for cloned checkout

PINNED = "2026.10.6"


@pytest.fixture(autouse=True)
def _english():
    """The source language, whatever the machine reads: these lines are English by construction."""
    i18n.set_language("en")
    yield
    i18n.set_language(None)


def clean_config() -> dict:
    """A configuration the engine itself would write, with one local model."""
    return {
        "plugins": {"allow": ["browser", "opencode-go", "ollama"], "entries": {}},
        "models": {"providers": {"ollama": {
            "baseUrl": "http://127.0.0.1:11434", "api": "ollama", "models": [{"id": "qwen3:8b"}],
        }}},
        "agents": {
            "ownership": "explicit",
            "defaults": {
                "models": {},
                "heartbeat": {"every": "0m"},
            },
            "entries": {
                "main": {"model": "opencode-go/deepseek-v4.1-flash"},
                "capo": {"model": "ollama/qwen3:8b"},
            },
        },
        "tools": {
            "exec": {"security": "allowlist", "ask": "on-miss"},
            "byProvider": {"ollama/qwen3:8b": {"profile": "messaging", "deny": ["write"]}},
            "agentToAgent": {"enabled": True, "allow": [["main", "capo"], ["capo", "main"]]},
        },
        "gateway": {
            "mode": "local", "port": 18789, "bind": "loopback",
            "auth": {"mode": "token", "token": "a-real-token-value"},
            "tailscale": {"mode": "off"},
        },
    }


def machine(config_path: Path, *, version: str = PINNED, installed: bool = True,
            go: bool = True, claude: bool = False, codex: bool = False,
            ollama: tuple[str, ...] = ("qwen3:8b",), companion: Companion | None = None) -> Detection:
    return Detection(
        openclaw=Installation(installed=installed, version=version, config_path=str(config_path), config_present=config_path.is_file()),
        claude=Login(binary=claude, login=True if claude else None),
        codex=Login(binary=codex, login=True if codex else None),
        opencode_go=go,
        local=LocalModels(ollama=tuple(LocalModelInfo(name=name) for name in ollama)),
        n8n=False,
        companion=companion or Companion(),
    )


def write_config(tmp_path: Path, config: dict | str) -> Path:
    path = tmp_path / "home" / ".openclaw" / "openclaw.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = config if isinstance(config, str) else json.dumps(config)
    path.write_text(text, encoding="utf-8")
    return path


def declare(vault: Path, state: str = "local") -> Path:
    target = vault / "03-INFRA" / "agent-universal-layer" / "modules.state.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"schema_version: 2\nmodules:\n  openclaw: {state}\n", encoding="utf-8")
    return vault


def run_doctor(tmp_path: Path, config_path: Path | None = None, *, declared: bool = True,
               platform: str = "linux", connect=None, detected: Detection | None = None,
               wsl_distributions=None, node_version=None, linger_dir=None, env=None, pending=None,
               **machine_kwargs):
    vault = declare(tmp_path / "vault") if declared else tmp_path / "vault"
    path = config_path or (tmp_path / "home" / ".openclaw" / "openclaw.json")
    snapshot = detected if detected is not None else machine(path, **machine_kwargs)
    values = {"LOCALAPPDATA": str(tmp_path / "localappdata"), "APPDATA": str(tmp_path / "appdata")}
    values.update(env or {})
    return collect(
        detected=snapshot,
        home=tmp_path / "home",
        vault_data=vault,
        engine_root=tmp_path / "engine",
        env=values,
        platform=platform,
        connect=connect or (lambda host, port, timeout: False),
        wsl_distributions=wsl_distributions or (lambda: []),
        node_version=node_version or (lambda: "v24.22.1"),
        linger_dir=linger_dir,
        pending=pending,
    )


def by_id(outcomes) -> dict:
    return {outcome.id: outcome for outcome in outcomes}


def test_a_machine_without_openclaw_and_without_the_module_says_nothing(tmp_path):
    outcomes = collect(
        home=tmp_path / "home",
        vault_data=tmp_path / "vault",
        engine_root=tmp_path / "engine",
        env={},
        platform="linux",
    )
    assert outcomes == []


def test_a_declared_module_that_is_not_installed_is_a_failure(tmp_path):
    outcomes = run_doctor(tmp_path, installed=False)
    installed = by_id(outcomes)["openclaw.installed"]
    assert installed.severity == Severity.BROKEN
    assert f"openclaw@{PINNED}" in installed.action


def test_a_clean_configuration_has_no_failure(tmp_path):
    outcomes = run_doctor(tmp_path, write_config(tmp_path, clean_config()))
    assert exit_code(outcomes) == 0
    assert not [o for o in outcomes if o.severity == Severity.BROKEN]
    ids = by_id(outcomes)
    assert ids["openclaw.config"].severity == Severity.OK
    assert ids["openclaw.cage"].severity == Severity.OK
    assert ids["openclaw.plugins.google"].severity == Severity.OK


def test_a_version_off_the_pin_warns_and_names_the_alignment_command(tmp_path):
    outcomes = run_doctor(tmp_path, write_config(tmp_path, clean_config()), version="2026.9.1")
    installed = by_id(outcomes)["openclaw.installed"]
    assert installed.severity == Severity.WARN
    assert f"npm install -g openclaw@{PINNED}" in installed.action


def test_an_unreadable_configuration_is_a_failure(tmp_path):
    path = write_config(tmp_path, "{not json")
    outcomes = run_doctor(tmp_path, path)
    assert by_id(outcomes)["openclaw.config"].severity == Severity.BROKEN


def test_a_missing_configuration_is_a_warning_not_a_failure(tmp_path):
    outcomes = run_doctor(tmp_path)
    assert by_id(outcomes)["openclaw.config"].severity == Severity.WARN
    assert exit_code(outcomes) == 0


def test_ollama_latest_is_a_failure(tmp_path):
    config = clean_config()
    config["models"]["providers"]["ollama"]["models"] = [{"id": "qwen3:8b:latest"}]
    outcomes = run_doctor(tmp_path, write_config(tmp_path, config))
    assert by_id(outcomes)["openclaw.ollama_latest"].severity == Severity.BROKEN


def test_a_local_model_without_a_cage_is_a_failure(tmp_path):
    config = clean_config()
    del config["tools"]["byProvider"]
    outcomes = run_doctor(tmp_path, write_config(tmp_path, config))
    assert by_id(outcomes)["openclaw.cage"].severity == Severity.BROKEN


def test_a_heartbeat_that_is_on_is_a_warning(tmp_path):
    config = clean_config()
    config["agents"]["defaults"]["heartbeat"]["every"] = "5m"
    outcomes = run_doctor(tmp_path, write_config(tmp_path, config))
    assert by_id(outcomes)["openclaw.heartbeat"].severity == Severity.WARN


def test_a_gateway_bound_past_the_home_network_or_without_a_token_is_a_failure(tmp_path):
    config = clean_config()
    config["gateway"]["bind"] = "0.0.0.0"
    config["gateway"]["auth"]["mode"] = "none"
    outcomes = by_id(run_doctor(tmp_path, write_config(tmp_path, config)))
    assert outcomes["openclaw.gateway.bind"].severity == Severity.BROKEN
    assert outcomes["openclaw.gateway.auth"].severity == Severity.BROKEN


def test_a_placeholder_token_is_a_failure_not_a_pass(tmp_path):
    """`setup` writes the placeholder only before it generates the real token."""
    config = clean_config()
    config["gateway"]["auth"]["token"] = PENDING_TOKEN
    outcomes = by_id(run_doctor(tmp_path, write_config(tmp_path, config)))
    assert outcomes["openclaw.gateway.auth"].severity == Severity.BROKEN
    assert "setup" in (outcomes["openclaw.gateway.auth"].action or "")


def test_an_empty_token_is_a_failure(tmp_path):
    config = clean_config()
    config["gateway"]["auth"]["token"] = "   "
    outcomes = by_id(run_doctor(tmp_path, write_config(tmp_path, config)))
    assert outcomes["openclaw.gateway.auth"].severity == Severity.BROKEN


def test_a_lan_bind_without_a_real_token_is_a_failure(tmp_path):
    """A gateway on the home network with a placeholder token is an open door."""
    config = clean_config()
    config["gateway"]["bind"] = "lan"
    config["gateway"]["auth"]["token"] = PENDING_TOKEN
    outcomes = by_id(run_doctor(tmp_path, write_config(tmp_path, config)))
    assert outcomes["openclaw.gateway.bind"].severity == Severity.BROKEN
    assert outcomes["openclaw.gateway.auth"].severity == Severity.BROKEN


def test_a_lan_bind_with_a_real_token_passes(tmp_path):
    config = clean_config()
    config["gateway"]["bind"] = "lan"
    outcomes = by_id(run_doctor(tmp_path, write_config(tmp_path, config)))
    assert outcomes["openclaw.gateway.bind"].severity == Severity.OK
    assert outcomes["openclaw.gateway.auth"].severity == Severity.OK


def test_a_llama_cpp_model_is_caged_like_an_ollama_one(tmp_path):
    """OpenClaw's plugin provider is `llama-cpp`, not the old `llamacpp` id."""
    config = clean_config()
    config["models"]["providers"]["llama-cpp"] = {
        "baseUrl": "http://127.0.0.1:8080/v1", "api": "openai-completions",
        "models": [{"id": "local-model"}],
    }
    config["tools"]["byProvider"]["llama-cpp/local-model"] = {"profile": "messaging", "deny": ["write"]}

    outcomes = by_id(run_doctor(tmp_path, write_config(tmp_path, config)))

    assert outcomes["openclaw.cage"].severity == Severity.OK


def test_a_llama_cpp_model_without_its_cage_is_a_failure(tmp_path):
    config = clean_config()
    config["models"]["providers"]["llama-cpp"] = {
        "baseUrl": "http://127.0.0.1:8080/v1", "api": "openai-completions",
        "models": [{"id": "local-model"}],
    }

    outcomes = by_id(run_doctor(tmp_path, write_config(tmp_path, config)))

    assert outcomes["openclaw.cage"].severity == Severity.BROKEN
    assert "llama-cpp/local-model" in outcomes["openclaw.cage"].message


def test_an_old_node_is_a_failure(tmp_path):
    outcomes = run_doctor(
        tmp_path, write_config(tmp_path, clean_config()), node_version=lambda: "v22.0.0",
    )

    node = by_id(outcomes)["openclaw.node"]
    assert node.severity == Severity.BROKEN
    assert ">=24.22.1" in node.message
    assert "nodejs.org" in (node.action or "")


def test_a_supported_node_passes(tmp_path):
    outcomes = run_doctor(
        tmp_path, write_config(tmp_path, clean_config()), node_version=lambda: "v24.22.1",
    )

    assert by_id(outcomes)["openclaw.node"].severity == Severity.OK


def test_an_unreadable_node_version_is_a_warning(tmp_path):
    outcomes = run_doctor(
        tmp_path, write_config(tmp_path, clean_config()), node_version=lambda: None,
    )

    assert by_id(outcomes)["openclaw.node"].severity == Severity.WARN


def test_lingering_off_is_a_warning_with_the_command(tmp_path):
    outcomes = run_doctor(
        tmp_path, write_config(tmp_path, clean_config()), platform="linux",
        env={"USER": "matte"}, linger_dir=tmp_path / "linger",
    )

    linger = by_id(outcomes)["openclaw.linger"]
    assert linger.severity == Severity.WARN
    assert "loginctl enable-linger matte" in (linger.action or "")


def test_lingering_on_passes(tmp_path):
    linger_dir = tmp_path / "linger"
    linger_dir.mkdir()
    (linger_dir / "matte").write_text("", encoding="utf-8")

    outcomes = run_doctor(
        tmp_path, write_config(tmp_path, clean_config()), platform="linux",
        env={"USER": "matte"}, linger_dir=linger_dir,
    )

    assert by_id(outcomes)["openclaw.linger"].severity == Severity.OK


def test_a_channel_without_a_binding_is_a_warning(tmp_path):
    config = clean_config()
    config["channels"] = {"telegram": {"accounts": {"default": {"botToken": "x"}}}}

    outcomes = by_id(run_doctor(tmp_path, write_config(tmp_path, config)))

    binding = outcomes["openclaw.bindings"]
    assert binding.severity == Severity.WARN
    assert "telegram" in binding.message
    assert "nexgen openclaw setup" in (binding.action or "")


def test_a_bound_channel_passes(tmp_path):
    config = clean_config()
    config["channels"] = {"telegram": {"accounts": {"default": {"botToken": "x"}}}}
    config["bindings"] = [{"agentId": "main", "match": {"channel": "telegram", "accountId": "*"}}]

    outcomes = by_id(run_doctor(tmp_path, write_config(tmp_path, config)))

    assert outcomes["openclaw.bindings"].severity == Severity.OK


def test_a_google_subscription_is_a_failure(tmp_path):
    config = clean_config()
    config["plugins"]["allow"].append("google-antigravity")
    outcomes = run_doctor(tmp_path, write_config(tmp_path, config))
    assert by_id(outcomes)["openclaw.plugins.google"].severity == Severity.BROKEN


def test_the_google_api_key_plugin_is_not_a_subscription(tmp_path):
    config = clean_config()
    config["plugins"]["allow"].append("google")
    outcomes = run_doctor(tmp_path, write_config(tmp_path, config))
    assert by_id(outcomes)["openclaw.plugins.google"].severity == Severity.OK


def test_the_gemini_cli_runtime_is_a_failure(tmp_path):
    config = clean_config()
    config["plugins"]["allow"].append("google")
    config["agents"]["defaults"]["models"]["google/gemini-3-pro"] = {"agentRuntime": {"id": "google-gemini-cli"}}
    outcomes = run_doctor(tmp_path, write_config(tmp_path, config))
    assert by_id(outcomes)["openclaw.plugins.google"].severity == Severity.BROKEN


def test_a_gemini_cli_model_route_is_a_failure(tmp_path):
    """No agentRuntime: the route's own prefix is what selects the backend."""
    config = clean_config()
    config["agents"]["defaults"]["models"]["google-gemini-cli/gemini-3-pro"] = {}
    outcomes = run_doctor(tmp_path, write_config(tmp_path, config))
    assert by_id(outcomes)["openclaw.plugins.google"].severity == Severity.BROKEN


def test_a_google_oauth_profile_is_a_failure(tmp_path):
    config = clean_config()
    config["auth"] = {"profiles": {"google-gemini-cli:me": {"provider": "google-gemini-cli", "mode": "oauth"}}}
    outcomes = run_doctor(tmp_path, write_config(tmp_path, config))
    assert by_id(outcomes)["openclaw.plugins.google"].severity == Severity.BROKEN


def local_agy_config(mode: str | None = "sandbox") -> dict:
    """The file that drives the local agy CLI, with the mode the plugin was given."""
    config = clean_config()
    config["plugins"]["allow"].append("google-antigravity-cli")
    entry: dict = {"config": {} if mode is None else {"permissionMode": mode}}
    config["plugins"]["entries"]["google-antigravity-cli"] = entry
    config["agents"]["defaults"]["models"]["google/gemini-3-pro"] = {
        "agentRuntime": {"id": "google-antigravity-cli"},
    }
    return config


def test_the_local_agy_cli_backend_is_not_a_subscription(tmp_path):
    outcomes = by_id(run_doctor(tmp_path, write_config(tmp_path, local_agy_config())))

    assert outcomes["openclaw.plugins.google"].severity == Severity.OK
    assert outcomes["openclaw.google.cli_backend"].severity == Severity.OK


def test_the_local_agy_cli_backend_approving_its_own_tools_is_a_warning(tmp_path):
    config = local_agy_config("skip")
    outcomes = by_id(run_doctor(tmp_path, write_config(tmp_path, config)))

    line = outcomes["openclaw.google.cli_backend"]
    assert line.severity == Severity.WARN
    assert "'skip'" in line.message
    assert "sandbox" in (line.action or "") and "settings" in (line.action or "")
    assert exit_code(list(outcomes.values())) == 0


@pytest.mark.parametrize("mode", ["sandbox", "settings", None])
def test_a_guarded_agy_permission_mode_passes(tmp_path, mode):
    outcomes = by_id(run_doctor(tmp_path, write_config(tmp_path, local_agy_config(mode))))

    assert outcomes["openclaw.google.cli_backend"].severity == Severity.OK


def test_an_agy_permission_mode_the_doctor_does_not_know_is_a_warning(tmp_path):
    outcomes = by_id(run_doctor(tmp_path, write_config(tmp_path, local_agy_config("auto"))))

    assert outcomes["openclaw.google.cli_backend"].severity == Severity.WARN


def test_the_local_agy_cli_backend_has_no_line_when_nobody_names_it(tmp_path):
    outcomes = by_id(run_doctor(tmp_path, write_config(tmp_path, clean_config())))

    assert "openclaw.google.cli_backend" not in outcomes


def test_a_gemini_seat_through_acp_is_a_warning(tmp_path):
    config = clean_config()
    config["acp"] = {"allowedAgents": ["gemini"]}
    outcomes = run_doctor(tmp_path, write_config(tmp_path, config))
    assert by_id(outcomes)["openclaw.acp.gemini"].severity == Severity.WARN


def test_an_agent_on_a_subscription_this_machine_lacks_is_a_warning(tmp_path):
    config = clean_config()
    config["agents"]["entries"]["capo"] = {"model": "anthropic/claude-opus-5-5"}
    outcomes = run_doctor(tmp_path, write_config(tmp_path, config), claude=False)
    agent_models = by_id(outcomes)["openclaw.agent_models"]
    assert agent_models.severity == Severity.WARN
    assert "anthropic/claude-opus-5-5" in agent_models.message


def test_a_stopped_gateway_is_only_information(tmp_path):
    outcomes = run_doctor(tmp_path, write_config(tmp_path, clean_config()))
    listening = by_id(outcomes)["openclaw.gateway.listening"]
    assert listening.severity == Severity.OK
    assert "18789" in listening.message


def test_every_agent_gets_a_learned_skills_line(tmp_path):
    outcomes = run_doctor(tmp_path, write_config(tmp_path, clean_config()))

    ids = [outcome.id for outcome in outcomes]
    assert "openclaw.workshop_skills.main" in ids
    assert "openclaw.workshop_skills.capo" in ids


def test_the_companion_gateway_is_a_warning_when_its_wsl_distro_exists(tmp_path):
    """Case (c): the native gateway is here and the app runs one of its own."""
    app_gateway = Companion(
        present=True, owns_local_gateway=True,
        signals=("the app-owned WSL distribution OpenClawGateway",),
    )
    outcomes = run_doctor(
        tmp_path, write_config(tmp_path, clean_config()), platform="win32",
        wsl_distributions=lambda: ["OpenClawGateway"], companion=app_gateway,
    )

    companion = by_id(outcomes)["openclaw.companion_gateway"]
    assert companion.severity == Severity.WARN
    assert "Connections" in (companion.action or "")


def test_an_app_gateway_alone_is_reported_through_the_real_probe(tmp_path, monkeypatch):
    """Case (b) end to end: the app's file is read, the snapshot is built, one line comes out."""
    settings = tmp_path / "roaming" / "OpenClawTray" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"gatewayUrl": "ws://127.0.0.1:52190"}), encoding="utf-8")
    real_detect = detect
    monkeypatch.setattr(
        "nexgen_core.openclaw.doctor.detect",
        lambda **kwargs: real_detect(
            which=lambda name: None, run=lambda *a, **k: None, http_json=lambda *a, **k: None, **kwargs,
        ),
    )

    outcomes = collect(
        home=tmp_path / "home", vault_data=tmp_path / "vault", engine_root=tmp_path / "engine",
        env={"APPDATA": str(tmp_path / "roaming")}, platform="win32", wsl_distributions=lambda: [],
    )

    assert [outcome.id for outcome in outcomes] == ["openclaw.companion_gateway"]
    assert outcomes[0].severity is Severity.OK
    assert exit_code(outcomes) == 0


def test_an_installed_app_with_no_gateway_of_its_own_costs_no_probe(tmp_path, monkeypatch):
    """The app can be pointed at this PC's gateway and own nothing: silence must be free."""

    def refuse(**kwargs):
        raise AssertionError("a machine with nothing to say must not build a snapshot")

    monkeypatch.setattr("nexgen_core.openclaw.doctor.detect", refuse)

    assert collect(
        home=tmp_path / "home", vault_data=tmp_path / "vault", engine_root=tmp_path / "engine",
        env={}, platform="win32", wsl_distributions=lambda: [],
    ) == []


def test_the_app_gateway_alone_is_informative_and_never_a_failure(tmp_path):
    """Case (b): one line, no failure, and no native check judging a gateway that is not there."""
    app_gateway = Companion(
        present=True, owns_local_gateway=True,
        signals=("the app-owned WSL distribution OpenClawGateway",),
    )
    outcomes = run_doctor(
        tmp_path, installed=False, declared=False, platform="win32", companion=app_gateway,
    )

    assert [outcome.id for outcome in outcomes] == ["openclaw.companion_gateway"]
    assert outcomes[0].severity is Severity.OK
    assert exit_code(outcomes) == 0


def test_an_installed_app_that_names_no_gateway_gets_the_connections_step(tmp_path):
    """The common machine: the exe is there, and one line says the step that connects it."""
    outcomes = run_doctor(
        tmp_path, write_config(tmp_path, clean_config()), platform="win32",
        companion=Companion(present=True),
    )

    line = by_id(outcomes)["openclaw.companion_gateway"]
    assert line.severity is Severity.OK
    assert "names no gateway on this PC" in line.message
    assert "a local Gateway on this PC" in (line.action or "")


def test_the_app_on_this_gateway_says_whether_its_device_is_listed(tmp_path):
    outcomes = run_doctor(
        tmp_path, write_config(tmp_path, clean_config()), platform="win32",
        companion=Companion(present=True, points_here=True, port=18789),
        pending=lambda: [{"id": "req-1", "summary": "OpenClaw Companion on Windows"}],
    )

    line = by_id(outcomes)["openclaw.companion_gateway"]
    assert "set to this PC's gateway on port 18789" in line.message
    assert "The gateway lists its device." in line.message


def test_a_second_gateway_from_the_microsoft_store_is_a_warning(tmp_path):
    package = tmp_path / "localappdata" / "Packages" / "OpenClaw_abc123"
    package.mkdir(parents=True)
    outcomes = run_doctor(tmp_path, write_config(tmp_path, clean_config()), platform="win32")
    store = by_id(outcomes)["openclaw.store_gateway"]
    assert store.severity == Severity.WARN
    assert "OpenClaw_abc123" in store.message


def test_isolated_native_gateway_package_is_not_treated_as_store_conflict_in_doctor(tmp_path):
    package = tmp_path / "localappdata" / "Packages" / "OpenClawFoundation.OpenClawGateway_rfcbke2p71se2"
    (package / "LocalState" / "OpenClawGatewayMSIX").mkdir(parents=True)
    gateways = tmp_path / "appdata" / "OpenClawTray" / "gateways.json"
    gateways.parent.mkdir(parents=True)
    gateways.write_text(json.dumps({
        "gateways": [
            {
                "id": "native-gw",
                "url": "ws://127.0.0.1:52190",
                "friendlyName": "Native Gateway",
                "isLocal": True,
                "nativePackageFamilyName": "OpenClawFoundation.OpenClawGateway_rfcbke2p71se2",
                "nativeRuntimeContract": "isolated-session-v1",
            }
        ],
        "activeId": "native-gw",
    }), encoding="utf-8")

    values = {"LOCALAPPDATA": str(tmp_path / "localappdata"), "APPDATA": str(tmp_path / "appdata")}
    comp = probe(tmp_path / "home", values, "win32")
    outcomes = run_doctor(tmp_path, write_config(tmp_path, clean_config()), platform="win32", companion=comp)
    assert "openclaw.store_gateway" not in by_id(outcomes)
    app_line = by_id(outcomes)["openclaw.companion_gateway"]
    assert app_line.severity == Severity.WARN
    assert "isolated native gateway" in app_line.message
    assert "turn off autostart for the native gateway" in (app_line.action or "")



def test_installed_but_undeclared_is_named_without_the_module(tmp_path):
    outcomes = run_doctor(tmp_path, write_config(tmp_path, clean_config()), declared=False)
    assert by_id(outcomes)["openclaw.declared"].severity == Severity.WARN


def test_an_empty_report_says_why_it_is_empty():
    from nexgen_core.openclaw.doctor import render

    text = render([])
    assert "OpenClaw" in text
    assert "PASS" not in text


def test_tool_search_active_by_default(tmp_path):
    outcomes = run_doctor(tmp_path, write_config(tmp_path, clean_config()))
    ts = by_id(outcomes)["openclaw.tool_search"]
    assert ts.severity == Severity.OK
    assert "Tool Search is active" in ts.message
    assert "default" in ts.message


def test_tool_search_explicitly_configured(tmp_path):
    cfg = clean_config()
    cfg["tools"]["toolSearch"] = {"mode": "directory"}
    outcomes = run_doctor(tmp_path, write_config(tmp_path, cfg))
    ts = by_id(outcomes)["openclaw.tool_search"]
    assert ts.severity == Severity.OK
    assert "directory" in ts.message


def test_tool_search_legacy_bare_false_warns(tmp_path):
    cfg = clean_config()
    cfg["tools"]["toolSearch"] = False
    outcomes = run_doctor(tmp_path, write_config(tmp_path, cfg))
    ts = by_id(outcomes)["openclaw.tool_search"]
    assert ts.severity == Severity.WARN
    assert "legacy engine setting" in ts.message
    assert "nexgen openclaw setup" in (ts.action or "")


def test_tool_search_user_disabled_with_few_mcp_servers_passes(tmp_path):
    cfg = clean_config()
    cfg["tools"]["toolSearch"] = {"enabled": False}
    cfg["mcp"] = {
        "servers": {
            "srv1": {"command": "node", "args": ["s1.js"]},
            "srv2": {"command": "node", "args": ["s2.js"]},
        }
    }
    outcomes = run_doctor(tmp_path, write_config(tmp_path, cfg))
    ts = by_id(outcomes)["openclaw.tool_search"]
    assert ts.severity == Severity.OK
    assert "disabled by user configuration" in ts.message


def test_tool_search_user_disabled_with_many_mcp_servers_warns(tmp_path):
    cfg = clean_config()
    cfg["tools"]["toolSearch"] = {"enabled": False}
    cfg["mcp"] = {
        "servers": {
            f"srv{i}": {"command": "node", "args": [f"s{i}.js"]}
            for i in range(4)
        }
    }
    outcomes = run_doctor(tmp_path, write_config(tmp_path, cfg))
    ts = by_id(outcomes)["openclaw.tool_search"]
    assert ts.severity == Severity.WARN
    assert "4 MCP servers" in ts.message
    assert "openclaw.json" in (ts.action or "")


def test_mcp_lazy_absent_passes(tmp_path):
    outcomes = run_doctor(tmp_path, write_config(tmp_path, clean_config()))
    lazy = by_id(outcomes)["openclaw.mcp.lazy"]
    assert lazy.severity == Severity.OK


def test_mcp_lazy_mounted_by_engine_fails(tmp_path):
    cfg = clean_config()
    cfg["mcp"] = {
        "servers": {
            "lazy-mcp": {
                "command": "python",
                "args": ["/path/to/lazy-mcp.py"],
                "env": {"LAZY_MCP_CLI": "openclaw"},
            }
        }
    }
    outcomes = run_doctor(tmp_path, write_config(tmp_path, cfg))
    lazy = by_id(outcomes)["openclaw.mcp.lazy"]
    assert lazy.severity == Severity.BROKEN
    assert "lazy-mcp" in lazy.message


def test_mcp_lazy_mounted_by_user_warns(tmp_path):
    cfg = clean_config()
    cfg["mcp"] = {
        "servers": {
            "lazy-mcp": {
                "command": "node",
                "args": ["custom-proxy.js"],
            }
        }
    }
    outcomes = run_doctor(tmp_path, write_config(tmp_path, cfg))
    lazy = by_id(outcomes)["openclaw.mcp.lazy"]
    assert lazy.severity == Severity.WARN
    assert "custom" in lazy.message.lower()

