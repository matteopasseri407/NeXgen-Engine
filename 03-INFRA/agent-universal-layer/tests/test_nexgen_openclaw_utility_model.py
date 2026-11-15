"""Which model answers OpenClaw's short internal work.

`agents.defaults.utilityModel` moves the calls nobody types -- generated session and
thread titles, progress narration, activity recaps, transcript summaries -- off the
agent's primary model. Left unset, each of those is a separate request billed to the
model the conversation runs on, which on this squad is a 78,717-token prompt.

These tests pin the order the engine chooses in, and pin that it writes nothing when the
machine has nothing cheaper or when the file already answers.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.openclaw.detect import Detection, Installation, LocalModelInfo, LocalModels, Login  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.profile import build_config  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.utility_model import (  # noqa: E402 - sys.path shim for cloned checkout
    declares_utility_model,
    utility_block,
    utility_ref,
)


def machine(
    *, claude: bool = False, codex: bool = False, go: bool = False,
    ollama: tuple[str, ...] = (), llamacpp: tuple[str, ...] = (),
) -> Detection:
    """One machine, described by the subscriptions and local backends it has."""
    return Detection(
        openclaw=Installation(installed=True, version="2026.11.2", config_path="/state/openclaw.json"),
        claude=Login(binary=claude, login=True if claude else None),
        codex=Login(binary=codex, login=True if codex else None),
        opencode_go=go,
        local=LocalModels(
            ollama=tuple(LocalModelInfo(name=name) for name in ollama),
            llamacpp=tuple(LocalModelInfo(name=name) for name in llamacpp),
        ),
        n8n=False,
    )


def test_a_machine_that_serves_a_local_model_uses_that_one():
    """Free, and the text never leaves the machine: `:latest` is a tag, not part of the id."""
    detected = machine(go=True, ollama=("qwen3:8b:latest",))

    assert utility_ref(detected) == "ollama/qwen3:8b"


def test_a_llamacpp_model_is_named_by_the_provider_openclaw_reads():
    assert utility_ref(machine(llamacpp=("Qwen3-8B-GGUF",))) == "llama-cpp/Qwen3-8B-GGUF"


def test_without_a_local_model_the_flat_flash_model_is_the_cheap_one():
    assert utility_ref(machine(go=True, claude=True)) == "opencode-go/deepseek-v4.1-flash"


def test_a_codex_only_machine_falls_back_to_that_subscription():
    assert utility_ref(machine(codex=True)) == "openai/gpt-6-luna"


def test_a_claude_only_machine_writes_no_key_at_all():
    """A Claude call is the scarce window, so the engine pins nothing rather than that."""
    detected = machine(claude=True)

    assert utility_ref(detected) is None
    assert utility_block(detected, None) == {}


def test_the_key_lands_in_agents_defaults():
    config = build_config(machine(ollama=("qwen3:8b",)))

    assert config["agents"]["defaults"]["utilityModel"] == "ollama/qwen3:8b"


def test_a_file_that_already_names_a_utility_model_is_left_alone():
    current = {"agents": {"defaults": {"utilityModel": "ollama/other:7b"}}}

    assert declares_utility_model(current) is True
    assert utility_block(machine(ollama=("qwen3:8b",)), current) == {}


def test_an_empty_string_disables_utility_routing_and_is_a_decision():
    """OpenClaw documents `""` as "do not route utility work elsewhere"; it is not a gap."""
    current = {"agents": {"defaults": {"utilityModel": ""}}}

    assert declares_utility_model(current) is True
    assert utility_block(machine(ollama=("qwen3:8b",)), current) == {}


def test_a_chain_the_person_wrote_is_read_as_a_decision():
    current = {"agents": {"defaults": {"utilityModel": {"primary": "ollama/qwen3:8b", "fallbacks": []}}}}

    assert declares_utility_model(current) is True
    assert utility_block(machine(go=True), current) == {}


def test_an_empty_mapping_answers_nothing_so_the_engine_proposes():
    current = {"agents": {"defaults": {"utilityModel": {}}}}

    assert declares_utility_model(current) is False
    assert utility_block(machine(go=True), current) == {"utilityModel": "opencode-go/deepseek-v4.1-flash"}


def test_no_proposed_ref_is_ever_a_claude_model():
    """The owner's window is the thing this exists to protect."""
    for detected in (
        machine(claude=True), machine(claude=True, codex=True),
        machine(claude=True, go=True), machine(claude=True, ollama=("qwen3:8b",)),
    ):
        config = build_config(detected)
        assert "anthropic/" not in json.dumps(config["agents"]["defaults"].get("utilityModel"))
