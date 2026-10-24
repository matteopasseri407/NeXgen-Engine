"""The doctor: a good configuration passes, a dangerous one is named.

The first test is the one that matters most: a machine that never chose
OpenClaw must not be told anything about it, because a doctor that invents work
is a doctor nobody reads.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.openclaw.companion import Companion  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.detect import Detection, Installation, LocalModelInfo, LocalModels, Login  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.doctor import collect, exit_code  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.profile import PENDING_TOKEN  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.report import Severity  # noqa: E402 - sys.path shim for cloned checkout

PINNED = "2026.10.6"


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
            "toolSearch": False,
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
               wsl_distributions=None, node_version=None, linger_dir=None, env=None, **machine_kwargs):
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


def test_a_google_oauth_profile_is_a_failure(tmp_path):
    config = clean_config()
    config["auth"] = {"profiles": {"google-gemini-cli:me": {"provider": "google-gemini-cli", "mode": "oauth"}}}
    outcomes = run_doctor(tmp_path, write_config(tmp_path, config))
    assert by_id(outcomes)["openclaw.plugins.google"].severity == Severity.BROKEN


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


def test_a_second_gateway_from_the_microsoft_store_is_a_warning(tmp_path):
    package = tmp_path / "localappdata" / "Packages" / "OpenClaw_abc123"
    package.mkdir(parents=True)
    outcomes = run_doctor(tmp_path, write_config(tmp_path, clean_config()), platform="win32")
    store = by_id(outcomes)["openclaw.store_gateway"]
    assert store.severity == Severity.WARN
    assert "OpenClaw_abc123" in store.message


def test_installed_but_undeclared_is_named_without_the_module(tmp_path):
    outcomes = run_doctor(tmp_path, write_config(tmp_path, clean_config()), declared=False)
    assert by_id(outcomes)["openclaw.declared"].severity == Severity.WARN


def test_an_empty_report_says_why_it_is_empty():
    from nexgen_core.openclaw.doctor import render

    text = render([])
    assert "OpenClaw" in text
    assert "PASS" not in text
