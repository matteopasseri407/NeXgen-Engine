"""The configuration proposed for each kind of machine.

`build_config` is a pure function, so these tests are about the decisions it
encodes: which plugin is allowed, which model each agent gets, when an agent is
not created at all, what the file in force must not be overwritten on, and the
three rules that are not preferences — no Google subscription ever enters
OpenClaw, a local model is always caged, and every model block carries its
context window.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.openclaw.detect import Detection, Installation, LocalModelInfo, LocalModels, Login  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.profile import Choices, build_config, denied_tools  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.templates import render_workspace  # noqa: E402 - sys.path shim for cloned checkout

BASE_PLUGINS = [
    "browser", "canvas", "device-pair", "document-extract", "file-transfer",
    "memory-core", "web-readability", "talk-voice", "tts-local-cli",
]

STATE = Path("/state")


def info(name: str, **facts) -> LocalModelInfo:
    return LocalModelInfo(name=name, **facts)


def machine(
    *,
    claude: bool = False,
    codex: bool = False,
    go: bool = False,
    ollama: tuple[str | LocalModelInfo, ...] = (),
    llamacpp: tuple[str | LocalModelInfo, ...] = (),
    n8n: bool = False,
    installed: bool = True,
    config_path: Path = STATE / "openclaw.json",
) -> Detection:
    """One machine, described by the subscriptions and backends it has."""
    def reported(values):
        return tuple(value if isinstance(value, LocalModelInfo) else info(value) for value in values)

    return Detection(
        openclaw=Installation(installed=installed, version="2026.10.6", config_path=str(config_path)),
        claude=Login(binary=claude, login=True if claude else None),
        codex=Login(binary=codex, login=True if codex else None),
        opencode_go=go,
        local=LocalModels(ollama=reported(ollama), llamacpp=reported(llamacpp)),
        n8n=n8n,
    )


def test_only_the_go_key_is_a_machine_that_uses_its_flat_plan():
    config = build_config(machine(go=True))
    assert config["plugins"]["allow"] == BASE_PLUGINS + ["opencode-go"]
    assert config["agents"]["entries"]["main"]["model"] == "opencode-go/deepseek-v4.1-flash"
    assert config["agents"]["entries"]["capo"]["model"] == "opencode-go/deepseek-v4-pro"
    assert "automazioni" not in config["agents"]["entries"]
    assert "anthropic" not in config["plugins"]["entries"]
    assert "ollama" not in config["plugins"]["entries"]
    assert "models" not in config


def test_claude_codex_go_and_ollama_together():
    config = build_config(machine(
        claude=True, codex=True, go=True, ollama=("qwen3:8b:latest",), n8n=True,
    ))
    allow = config["plugins"]["allow"]
    assert allow == BASE_PLUGINS + ["anthropic", "codex", "openai", "opencode-go", "ollama"]

    entries = config["plugins"]["entries"]
    assert entries["anthropic"]["config"]["sessionCatalog"]["enabled"] is False
    codex_app = entries["codex"]["config"]["appServer"]
    assert codex_app == {
        "homeScope": "user", "approvalPolicy": "on-request",
        "sandbox": "workspace-write", "approvalsReviewer": "user",
    }
    assert entries["ollama"] == {"enabled": True}

    defaults = config["agents"]["defaults"]
    assert defaults["models"] == {
        "anthropic/claude-opus-5-5": {"agentRuntime": {"id": "claude-cli"}},
        "openai/gpt-6-luna": {"agentRuntime": {"id": "codex"}},
    }
    assert defaults["heartbeat"] == {"every": "0m"}
    assert config["agents"]["ownership"] == "explicit"

    agents = config["agents"]["entries"]
    assert agents["main"]["model"] == "opencode-go/deepseek-v4.1-flash"
    assert agents["capo"]["model"] == "anthropic/claude-opus-5-5"
    assert agents["capo"]["thinkingDefault"] == "high"
    assert agents["capo"]["runtime"] == {"type": "embedded"}
    assert agents["automazioni"]["model"] == "opencode-go/deepseek-v4-pro"


def test_only_a_local_llamacpp_leaves_no_agent_but_the_assistant():
    config = build_config(machine(llamacpp=("devstral-small",)))
    agents = config["agents"]["entries"]
    assert list(agents) == ["main"]
    assert agents["main"]["model"] == "llama-cpp/devstral-small"
    assert Path(agents["main"]["workspace"]) == STATE / "workspace"
    provider = config["models"]["providers"]["llama-cpp"]
    assert provider["api"] == "openai-completions"
    assert provider["baseUrl"].endswith("/v1")
    assert provider["request"] == {"allowPrivateNetwork": True}
    assert provider["models"][0]["id"] == "devstral-small"
    assert provider["models"][0]["compat"]["toolSchemaProfile"] == "llamacpp"
    assert "llama-cpp" in config["plugins"]["allow"]
    assert config["plugins"]["entries"]["llama-cpp"] == {"enabled": True}


def test_an_ollama_model_does_not_get_the_llama_cpp_schema_profile():
    """The profile exists for llama.cpp's GBNF compiler; Ollama does not use it."""
    config = build_config(machine(ollama=("one",)))

    entry = config["models"]["providers"]["ollama"]["models"][0]

    assert "toolSchemaProfile" not in entry["compat"]


def test_a_llama_cpp_plugin_the_person_enabled_stays_on_when_the_server_is_off():
    """Switching a backend off is the person's choice, not the engine's."""
    current = {"plugins": {
        "allow": ["llama-cpp"],
        "entries": {"llama-cpp": {"enabled": True}},
    }}

    config = build_config(machine(), current=current)

    assert "llama-cpp" in config["plugins"]["allow"]
    assert config["plugins"]["entries"]["llama-cpp"] == {"enabled": True}
    assert "models" not in config


def test_a_machine_with_no_model_creates_no_agent_and_says_nothing_it_cannot_back():
    config = build_config(machine())
    assert config["agents"]["entries"] == {}
    assert "agentToAgent" not in config["tools"]
    assert config["tools"]["byProvider"] == {}


def test_the_go_key_wins_the_assistant_and_claude_wins_the_code_agent():
    config = build_config(machine(claude=True, codex=True, go=True, ollama=("qwen3:8b",), n8n=True))
    agents = config["agents"]["entries"]
    assert agents["main"]["model"] == "opencode-go/deepseek-v4.1-flash"
    assert agents["capo"]["model"] == "anthropic/claude-opus-5-5"


def test_with_claude_out_of_the_way_the_assistant_prefers_local_then_codex():
    local = build_config(machine(codex=True, ollama=("qwen3:8b",)))
    assert local["agents"]["entries"]["main"]["model"] == "ollama/qwen3:8b"
    cloud = build_config(machine(codex=True))
    assert cloud["agents"]["entries"]["main"]["model"] == "openai/gpt-6-luna"
    assert cloud["agents"]["entries"]["capo"]["model"] == "openai/gpt-6-luna"


def test_the_automation_agent_needs_n8n_and_falls_back_to_claude():
    with_n8n = build_config(machine(claude=True, n8n=True))
    assert with_n8n["agents"]["entries"]["automazioni"]["model"] == "anthropic/claude-opus-5-5"
    without = build_config(machine(claude=True))
    assert "automazioni" not in without["agents"]["entries"]


def test_ollama_latest_is_stripped_everywhere_and_the_provider_keeps_the_exact_id():
    config = build_config(machine(ollama=("qwen3:8b:latest",)))
    assert [model["id"] for model in config["models"]["providers"]["ollama"]["models"]] == ["qwen3:8b"]
    assert config["agents"]["entries"]["main"]["model"] == "ollama/qwen3:8b"
    assert "ollama/qwen3:8b" in config["tools"]["byProvider"]
    assert not any(":latest" in ref for ref in config["tools"]["byProvider"])


def test_an_ollama_block_carries_everything_openclaw_needs():
    config = build_config(machine(ollama=(
        info("qwen3-vl:8b", context_window=16384, vision=True, reasoning=True),
    )))
    entry = config["models"]["providers"]["ollama"]["models"][0]
    assert entry["id"] == "qwen3-vl:8b"
    assert entry["name"] == "qwen3-vl:8b"
    assert entry["reasoning"] is True
    assert entry["input"] == ["text", "image"]
    assert entry["contextWindow"] == 16384
    assert entry["contextTokens"] == 16384
    assert entry["maxTokens"] == 8192
    assert entry["cost"] == {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}
    assert entry["compat"] == {
        "supportsTools": True,
        "supportsUsageInStreaming": True,
        "supportsJsonSchemaResponseFormat": True,
    }


def test_a_text_only_model_gets_text_input_and_no_invented_window():
    config = build_config(machine(llamacpp=("local",)))
    entry = config["models"]["providers"]["llama-cpp"]["models"][0]
    assert entry["input"] == ["text"]
    assert "contextWindow" not in entry
    assert "contextTokens" not in entry


def test_an_existing_model_block_keeps_its_fields_and_gains_the_missing_ones():
    current = {"models": {"providers": {"ollama": {"models": [
        {"id": "qwen3:8b", "name": "il mio nome", "contextWindow": 999},
    ]}}}}
    config = build_config(machine(ollama=(info("qwen3:8b", context_window=16384),)), current=current)
    entry = config["models"]["providers"]["ollama"]["models"][0]
    assert entry["name"] == "il mio nome"
    assert entry["contextWindow"] == 999
    assert entry["contextTokens"] == 16384
    assert entry["maxTokens"] == 8192


def test_every_local_model_carries_the_cage():
    config = build_config(machine(ollama=("one",), llamacpp=("two",)))
    cage = config["tools"]["byProvider"]
    assert set(cage) == {"ollama/one", "llama-cpp/two"}
    deny = cage["ollama/one"]["deny"]
    assert cage["ollama/one"]["profile"] == "messaging"
    assert cage["ollama/one"]["alsoAllow"] == ["read", "view_image", "pdf", "tts", "memory_search", "memory_get"]
    for pattern in ("group:web", "group:runtime", "write", "edit", "apply_patch", "skill_workshop",
                    "playwright__*"):
        assert pattern in deny
    assert "lazy-mcp__lazy_mutate" not in deny
    for pattern in ("n8n-mcp__create_*", "n8n-mcp__delete_*", "vault-library__update_section",
                    "drive__drive_confirm_upload"):
        assert pattern in deny


def test_firecrawl_keeps_search_and_loses_the_tools_that_fetch_or_drive_a_page():
    deny = list(denied_tools())
    for pattern in ("firecrawl__*scrape*", "firecrawl__*map*", "firecrawl__*extract*",
                    "firecrawl__*crawl*", "firecrawl__*agent*", "firecrawl__*browser*",
                    "firecrawl__*interact*"):
        assert pattern in deny
    assert "firecrawl__*search*" not in deny


def test_no_google_anywhere():
    config = build_config(machine(claude=True, codex=True, go=True, ollama=("m",)))
    assert "google" not in json.dumps(config).lower()
    assert "gemini" not in json.dumps(config).lower()
    assert "antigravity" not in json.dumps(config).lower()


def test_acpx_is_opt_in_only():
    assert "acpx" not in build_config(machine(go=True))["plugins"]["allow"]
    assert "acpx" in build_config(machine(go=True), Choices(acpx=True))["plugins"]["allow"]


def test_the_gateway_is_loopback_and_token_protected_by_default():
    gateway = build_config(machine(go=True))["gateway"]
    assert gateway["mode"] == "local"
    assert gateway["port"] == 18789
    assert gateway["bind"] == "loopback"
    assert gateway["auth"]["mode"] == "token"
    assert gateway["auth"]["token"] == "generated-by-nexgen-setup"
    assert gateway["tailscale"] == {"mode": "off"}
    assert build_config(machine(go=True), Choices(lan=True))["gateway"]["bind"] == "lan"


def test_a_configured_channel_gets_a_binding_to_the_main_agent():
    """With explicit ownership a channel without a binding reaches no agent."""
    current = {
        "channels": {"telegram": {"accounts": {"default": {"botToken": "x"}}}},
        "bindings": [{"agentId": "support", "match": {"channel": "discord", "accountId": "*"}}],
    }

    config = build_config(machine(go=True), current=current)

    assert config["bindings"] == [
        {"agentId": "main", "match": {"channel": "telegram", "accountId": "*"}},
    ]


def test_a_channel_the_person_already_bound_is_not_proposed():
    current = {
        "channels": {"telegram": {"accounts": {"default": {"botToken": "x"}}}},
        "bindings": [{"agentId": "support", "match": {"channel": "telegram", "accountId": "*"}}],
    }

    config = build_config(machine(go=True), current=current)

    assert "bindings" not in config


def test_a_disabled_channel_gets_no_binding():
    current = {"channels": {"telegram": {"enabled": False, "accounts": {}}}}

    config = build_config(machine(go=True), current=current)

    assert "bindings" not in config


def test_a_tailscale_the_person_turned_on_is_not_switched_off():
    """A setup must not silently disable a remote access the person configured."""
    current = {"gateway": {"tailscale": {"mode": "serve"}}}

    gateway = build_config(machine(go=True), current=current)["gateway"]

    assert gateway["tailscale"] == {"mode": "serve"}


def test_a_server_keeps_loopback_and_lets_tailscale_serve_publish_it():
    """The pair OpenClaw documents for a machine with no screen: no port is opened."""
    gateway = build_config(machine(go=True), Choices(serve=True))["gateway"]

    assert gateway["bind"] == "loopback"
    assert gateway["tailscale"] == {"mode": "serve"}


def test_a_server_keeps_the_rest_of_the_tailscale_section_the_person_wrote():
    current = {"gateway": {"tailscale": {"mode": "off", "preserveFunnel": True}}}

    gateway = build_config(machine(go=True), Choices(serve=True), current=current)["gateway"]

    assert gateway["tailscale"] == {"mode": "serve", "preserveFunnel": True}


def test_a_token_already_in_the_file_is_never_replaced():
    current = {"gateway": {"auth": {"mode": "token", "token": "a-real-token"}}}
    gateway = build_config(machine(go=True), current=current)["gateway"]
    assert gateway["auth"]["token"] == "a-real-token"


def test_a_new_agent_gets_an_absolute_workspace_and_an_existing_one_keeps_its_own():
    current = {"agents": {"entries": {"main": {"workspace": "C:\\mine\\assistente"}}}}
    agents = build_config(machine(go=True, n8n=True), current=current)["agents"]["entries"]
    assert agents["main"]["workspace"] == "C:\\mine\\assistente"
    assert Path(agents["capo"]["workspace"]) == STATE / "workspace-capo"
    assert Path(agents["automazioni"]["workspace"]) == STATE / "workspace-automazioni"


def test_the_agents_are_named_and_introduced_in_the_engine_language():
    italian = build_config(machine(go=True, n8n=True), Choices(lang="it"))["agents"]["entries"]
    assert italian["main"]["name"] == "main"
    assert italian["main"]["identity"] == {"name": "assistente"}
    assert italian["capo"]["identity"] == {"name": "capo"}
    assert italian["automazioni"]["identity"] == {"name": "automazioni"}
    assert "agentDir" not in italian["main"]

    english = build_config(machine(go=True, n8n=True), Choices(lang="en"))["agents"]["entries"]
    assert english["main"]["identity"] == {"name": "assistant"}
    assert english["capo"]["identity"] == {"name": "lead"}
    assert english["automazioni"]["identity"] == {"name": "automations"}


def test_the_agents_may_talk_only_to_the_pairs_that_exist():
    """OpenClaw wants agent ids in `allow` (a list of pairs is refused at start) and the pairs per agent in `send`."""
    config = build_config(machine(go=True, n8n=True))
    assert config["tools"]["agentToAgent"] == {"enabled": True, "allow": ["automazioni", "capo", "main"]}
    assert all(isinstance(item, str) for item in config["tools"]["agentToAgent"]["allow"])
    entries = config["agents"]["entries"]
    assert entries["main"]["tools"]["agentToAgent"]["send"] == ["capo", "automazioni"]
    assert entries["capo"]["tools"]["agentToAgent"]["send"] == ["main"]
    assert entries["automazioni"]["tools"]["agentToAgent"]["send"] == ["main"]
    alone = build_config(machine(llamacpp=("only",)))
    assert "agentToAgent" not in alone["tools"]


def test_exec_is_allowlisted_and_tool_search_is_not_written():
    tools = build_config(machine(go=True))["tools"]
    assert "toolSearch" not in tools
    assert tools["exec"] == {"security": "allowlist", "ask": "on-miss"}


def test_the_writer_tools_are_one_table_and_the_cage_is_derived_from_it():
    patterns = denied_tools()
    assert "n8n-mcp__create_*" in patterns
    assert len(patterns) == len(set(patterns))


def test_every_agent_has_a_manual_in_both_languages():
    for language in ("it", "en"):
        for agent in ("main", "assistente", "capo", "automazioni"):
            files = render_workspace(agent, language)
            assert "AGENTS.md" in files
            assert files["AGENTS.md"].strip()
            assert "nexgen council" in files["AGENTS.md"]
            assert "vault-library__" in files["AGENTS.md"]


def test_the_code_agent_also_gets_the_files_claude_code_reads():
    files = render_workspace("capo", "it")
    assert json.loads(files[".claude/settings.json"]) == {"model": "claude-opus-5-5", "effortLevel": "high"}
    assert "AGENTS.md" in files["CLAUDE.md"]
    assert set(render_workspace("main", "en")) == {"AGENTS.md"}


def test_every_manual_says_how_work_is_handed_over():
    for agent in ("main", "capo", "automazioni"):
        text = render_workspace(agent, "it")["AGENTS.md"]
        assert "sessions_send" in text


def test_the_manuals_carry_no_path_of_this_machine():
    for agent in ("main", "capo", "automazioni"):
        text = render_workspace(agent, "en")["AGENTS.md"]
        assert "C:\\" not in text
        assert "/home/" not in text
        assert "matte" not in text.lower()


def test_the_owner_approved_agy_backend_stays_allowed() -> None:
    """The local agy CLI backend the owner installed survives a setup; no other Google plugin is kept."""
    current = {"plugins": {
        "allow": ["google-antigravity-cli", "google"],
        "entries": {"google-antigravity-cli": {"enabled": True}, "google": {"enabled": True}},
    }}

    allow = build_config(machine(claude=True), current=current)["plugins"]["allow"]

    assert "google-antigravity-cli" in allow
    assert "google" not in allow
    assert "google-antigravity-cli" not in build_config(machine(claude=True))["plugins"]["allow"]


def test_the_approved_agy_backend_gets_a_guarded_permission_mode() -> None:
    """`agy` approves its own tool calls unless the plugin is told to keep a boundary."""
    current = {"plugins": {
        "allow": ["google-antigravity-cli"],
        "entries": {"google-antigravity-cli": {"enabled": True}},
    }}

    entries = build_config(machine(), current=current)["plugins"]["entries"]

    assert entries["google-antigravity-cli"] == {"config": {"permissionMode": "sandbox"}}
    assert "enabled" not in entries["google-antigravity-cli"], "the engine never turns a plugin on or off"


@pytest.mark.parametrize("declared", ["sandbox", "settings"])
def test_a_guarded_mode_the_person_chose_is_kept(declared: str) -> None:
    current = {"plugins": {
        "allow": ["google-antigravity-cli"],
        "entries": {"google-antigravity-cli": {"enabled": True, "config": {"permissionMode": declared}}},
    }}

    entries = build_config(machine(), current=current)["plugins"]["entries"]

    assert entries["google-antigravity-cli"]["config"]["permissionMode"] == declared


def test_a_skip_permission_mode_is_never_carried_into_the_proposal() -> None:
    """The one mode that lets `agy` approve every tool call is replaced, not preserved."""
    current = {"plugins": {
        "allow": ["google-antigravity-cli"],
        "entries": {"google-antigravity-cli": {"enabled": True, "config": {"permissionMode": "skip"}}},
    }}

    entries = build_config(machine(), current=current)["plugins"]["entries"]

    assert entries["google-antigravity-cli"]["config"]["permissionMode"] == "sandbox"


def test_a_backend_nobody_approved_gets_no_entry_and_no_mode() -> None:
    entries = build_config(machine())["plugins"]["entries"]

    assert "google-antigravity-cli" not in entries


