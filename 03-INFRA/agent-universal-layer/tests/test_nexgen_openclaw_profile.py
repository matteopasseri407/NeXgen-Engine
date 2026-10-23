"""The configuration proposed for each kind of machine.

`build_config` is a pure function, so these tests are about the decisions it
encodes: which plugin is allowed, which model each agent gets, when an agent is
not created at all, and the two rules that are not preferences — no Google
subscription ever enters OpenClaw, and a local model is always caged.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.openclaw.detect import Detection, Installation, LocalModels, Login
from nexgen_core.openclaw.profile import Choices, build_config, denied_tools
from nexgen_core.openclaw.templates import render_workspace

BASE_PLUGINS = [
    "browser", "canvas", "device-pair", "document-extract", "file-transfer",
    "memory-core", "web-readability", "talk-voice", "tts-local-cli",
]


def machine(
    *,
    claude: bool = False,
    codex: bool = False,
    go: bool = False,
    ollama: tuple[str, ...] = (),
    llamacpp: tuple[str, ...] = (),
    n8n: bool = False,
    installed: bool = True,
) -> Detection:
    """One machine, described by the subscriptions and backends it has."""
    return Detection(
        openclaw=Installation(installed=installed, version="2026.10.6", config_path="/x/openclaw.json"),
        claude=Login(binary=claude, login=True if claude else None),
        codex=Login(binary=codex, login=True if codex else None),
        opencode_go=go,
        local=LocalModels(ollama=ollama, llamacpp=llamacpp),
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
    assert agents["capo"] == {
        "model": "anthropic/claude-opus-5-5",
        "workspace": "capo",
        "thinkingDefault": "high",
        "runtime": {"type": "embedded"},
    }
    assert agents["automazioni"]["model"] == "opencode-go/deepseek-v4-pro"


def test_only_a_local_llamacpp_leaves_no_agent_but_the_assistant():
    config = build_config(machine(llamacpp=("devstral-small",)))
    agents = config["agents"]["entries"]
    assert agents == {"main": {"model": "llamacpp/devstral-small", "workspace": "assistente"}}
    provider = config["models"]["providers"]["llamacpp"]
    assert provider["api"] == "openai-completions"
    assert provider["baseUrl"].endswith("/v1")
    assert provider["models"] == [{"id": "devstral-small"}]


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
    assert config["models"]["providers"]["ollama"]["models"] == [{"id": "qwen3:8b"}]
    assert config["agents"]["entries"]["main"]["model"] == "ollama/qwen3:8b"
    assert "ollama/qwen3:8b" in config["tools"]["byProvider"]
    assert not any(":latest" in ref for ref in config["tools"]["byProvider"])


def test_every_local_model_carries_the_cage():
    config = build_config(machine(ollama=("one",), llamacpp=("two",)))
    cage = config["tools"]["byProvider"]
    assert set(cage) == {"ollama/one", "llamacpp/two"}
    deny = cage["ollama/one"]["deny"]
    assert cage["ollama/one"]["profile"] == "messaging"
    assert cage["ollama/one"]["alsoAllow"] == ["read", "view_image", "pdf", "tts", "memory_search", "memory_get"]
    for pattern in ("group:web", "group:runtime", "write", "edit", "apply_patch", "playwright__*",
                    "lazy-mcp__lazy_mutate"):
        assert pattern in deny
    for pattern in ("n8n-mcp__create_*", "n8n-mcp__delete_*", "vault-library__update_section",
                    "drive__drive_confirm_upload"):
        assert pattern in deny


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


def test_the_agents_may_talk_only_to_the_pairs_that_exist():
    config = build_config(machine(go=True, n8n=True))
    assert config["tools"]["agentToAgent"] == {
        "enabled": True,
        "allow": [["main", "capo"], ["capo", "main"], ["main", "automazioni"], ["automazioni", "main"]],
    }
    alone = build_config(machine(llamacpp=("only",)))
    assert "agentToAgent" not in alone["tools"]


def test_exec_is_allowlisted_and_tool_search_is_off():
    tools = build_config(machine(go=True))["tools"]
    assert tools["toolSearch"] is False
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
