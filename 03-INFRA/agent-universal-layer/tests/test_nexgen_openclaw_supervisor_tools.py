"""Who may drive the supervision: the supervisor agent, and no other.

The plugin registers the state-changing tools `optional`, which in OpenClaw means
"not exposed until an operator allowlists the exact name". This is the operator:
`build_config` writes that allowlist on one agent. A person who already wrote
their own `tools.allow` for that agent keeps it -- OpenClaw refuses both keys in
the same scope -- and the plan says what to add there instead.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.openclaw.detect import Detection, Installation, LocalModels, Login  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.plan import _supervisor_tool_note  # noqa: E402 - one line of the plan, tested where it is decided
from nexgen_core.openclaw.profile import (  # noqa: E402
    SUPERVISOR_TOOLS,
    Choices,
    build_config,
)

STATE = Path("/state")


def machine(*, claude: bool = True, go: bool = True) -> Detection:
    return Detection(
        openclaw=Installation(installed=True, version="2026.10.6", config_path=str(STATE / "openclaw.json")),
        claude=Login(binary=claude, login=True if claude else None),
        codex=Login(binary=False, login=None),
        opencode_go=go,
        local=LocalModels(),
        n8n=False,
    )


def test_the_single_agent_is_the_supervisor_and_can_change_the_engine_state():
    config = build_config(machine(), Choices())
    entries = config["agents"]["entries"]

    assert list(entries) == ["main"]
    assert entries["main"]["tools"]["alsoAllow"] == sorted(SUPERVISOR_TOOLS)
    for agent_id, entry in entries.items():
        if agent_id == "main":
            continue
        assert not set(SUPERVISOR_TOOLS) & set(entry.get("tools", {}).get("alsoAllow", []))


def test_the_allowlist_does_not_erase_the_supervisor_tools():
    """One key must not replace the other, and the engine writes no messaging list at all."""
    config = build_config(machine(), Choices())

    tools = config["agents"]["entries"]["main"]["tools"]
    assert "agentToAgent" not in tools
    assert tools["alsoAllow"] == sorted(SUPERVISOR_TOOLS)


def test_a_machine_with_no_model_creates_no_agent_at_all():
    config = build_config(machine(claude=False, go=False), Choices())

    assert config.get("agents", {}).get("entries", {}) == {}


def test_a_person_allow_list_is_never_contradicted():
    """OpenClaw refuses `allow` and `alsoAllow` in the same scope: the engine steps back."""
    current = {
        "agents": {"entries": {"capo": {"tools": {"allow": ["read", "exec"]}}}},
    }

    config = build_config(machine(), Choices(), current)

    # The person's supervisor is kept as it is; the new file writes no tools beside it.
    assert "tools" not in config["agents"]["entries"]["main"]


def test_the_plan_names_the_tools_to_add_by_hand():
    current = {"agents": {"entries": {"capo": {"tools": {"allow": ["read"]}}}}}

    note = _supervisor_tool_note(current)

    assert note is not None
    for name in SUPERVISOR_TOOLS:
        assert name in note


def test_the_plan_says_nothing_when_it_writes_the_allowlist_itself():
    assert _supervisor_tool_note(None) is None
    assert _supervisor_tool_note({"agents": {"entries": {"capo": {"name": "capo"}}}}) is None
