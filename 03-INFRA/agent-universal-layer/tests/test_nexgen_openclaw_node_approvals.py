"""The doctor's line about the exec approvals of the PC an agent runs on.

It answers the failure a node gives when nothing there can approve a command:
`exec denied: request-failed`. The node query is the read-only one OpenClaw
documents, and no test runs `openclaw`: the reader is a fake, so each case is a
document and an answer, not a machine.

The document each case feeds is the shape `openclaw approvals get --node --json`
prints: the node's own policy, and the `effectivePolicy` scopes where OpenClaw
has already merged it with what the agent asks for. The merge is a literal in
every case here, because that is what makes the engine's judgement readable.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import i18n  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw import node_approvals  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.detect import Detection, Installation, LocalModels, Login  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.doctor import collect  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.node_approvals import (  # noqa: E402 - sys.path shim for cloned checkout
    NEVER_PROMPT_BODY,
    Reading,
    check_agents,
)
from nexgen_core.report import Severity  # noqa: E402 - sys.path shim for cloned checkout

PINNED = "2026.10.6"


@pytest.fixture(autouse=True)
def _english():
    i18n.set_language("en")
    yield
    i18n.set_language(None)


def config_with(agent_exec: dict | None = None, shared: dict | None = None) -> dict:
    """A gateway configuration with one agent that runs its commands somewhere."""
    return {
        "agents": {
            "ownership": "explicit",
            "entries": {"capo": {"name": "capo", "tools": {"exec": agent_exec or {}}}},
        },
        "tools": {"exec": shared or {"security": "allowlist", "ask": "on-miss"}},
    }


def on_node(**extra: object) -> dict:
    return {"host": "node", "node": "win-pc", **extra}


def merged(
    agent_id: str = "capo",
    security: tuple[str, str] = ("full", "full"),
    ask: tuple[str, str] = ("off", "off"),
    fallback: str = "full",
) -> dict:
    """One agent scope of the merged view, as the command prints it.

    Each pair is `(asked, effective)`: what the agent requests, and what OpenClaw
    computed once the node's own policy was intersected with it.
    """
    return {
        "path": "C:\\Users\\someone\\.openclaw\\state\\openclaw.sqlite#exec_approvals_config",
        "exists": True,
        "file": {"version": 1},
        "effectivePolicy": {
            "scopes": [{
                "scopeLabel": f"agent:{agent_id}",
                "configPath": f"agents.entries.{agent_id}.tools.exec",
                "agentId": agent_id,
                "security": {"requested": security[0], "effective": security[1]},
                "ask": {"requested": ask[0], "effective": ask[1]},
                "askFallback": {"effective": fallback},
            }],
        },
    }


def also_on(document: dict, agent_id: str, **policy: object) -> dict:
    """The same answer with a second agent's scope: one node, several agents."""
    document["effectivePolicy"]["scopes"].append(merged(agent_id, **policy)["effectivePolicy"]["scopes"][0])
    return document


def by_id(outcomes) -> dict:
    return {outcome.id: outcome for outcome in outcomes}


def answer(payload: dict) -> node_approvals.Reader:
    return lambda node: Reading(document=payload)


# ----------------------------------------------------------------- the comparison


def test_a_node_as_loose_as_the_agent_asks_for_is_not_a_warning():
    outcomes = check_agents(
        config_with(on_node(security="full", ask="off")),
        answer(merged(security=("full", "full"), ask=("off", "off"))),
    )

    assert by_id(outcomes)["openclaw.nodes.exec.capo"].severity == Severity.OK


def test_a_node_that_does_not_tighten_is_not_a_warning_either():
    """A host policy looser than the request leaves `effective` equal to the request."""
    outcomes = check_agents(
        config_with(on_node(security="allowlist", ask="on-miss")),
        answer(merged(security=("allowlist", "allowlist"), ask=("on-miss", "on-miss"))),
    )

    assert by_id(outcomes)["openclaw.nodes.exec.capo"].severity == Severity.OK


def test_a_node_whose_allowlist_stops_the_agent_is_a_warning_with_the_documented_body():
    outcomes = check_agents(
        config_with(on_node(security="full", ask="off")),
        answer(merged(security=("full", "allowlist"), ask=("off", "on-miss"), fallback="deny")),
    )

    outcome = by_id(outcomes)["openclaw.nodes.exec.capo"]
    assert outcome.severity == Severity.WARN
    assert "security allowlist instead of full" in outcome.message
    assert "ask on-miss instead of off" in outcome.message
    assert "openclaw approvals set --stdin" in (outcome.action or "")
    assert "openclaw approvals set --node win-pc --stdin" in (outcome.action or "")
    assert NEVER_PROMPT_BODY in (outcome.action or "")
    # The body is the one OpenClaw's documentation gives, not one this engine made up.
    assert json.loads(NEVER_PROMPT_BODY)["defaults"] == {
        "security": "full", "ask": "off", "askFallback": "full",
    }


def test_a_prompt_nobody_can_answer_is_named_even_when_the_agent_asks_for_one():
    """`on-miss` plus a `deny` fallback is the live case: the agent asks, nobody answers."""
    outcomes = check_agents(
        config_with(on_node(security="allowlist", ask="on-miss")),
        answer(merged(security=("allowlist", "allowlist"), ask=("on-miss", "on-miss"), fallback="deny")),
    )

    outcome = by_id(outcomes)["openclaw.nodes.exec.capo"]
    assert outcome.severity == Severity.WARN
    assert "askFallback deny" in outcome.message
    assert "answered by nobody" in outcome.message


def test_a_fallback_that_allows_the_run_raises_no_warning():
    outcomes = check_agents(
        config_with(on_node(security="allowlist", ask="on-miss")),
        answer(merged(security=("allowlist", "allowlist"), ask=("on-miss", "on-miss"), fallback="full")),
    )

    assert by_id(outcomes)["openclaw.nodes.exec.capo"].severity == Severity.OK


def test_an_agent_is_judged_by_its_own_scope_and_not_by_another_agents():
    """One node answers for every agent that runs on it: the scopes are per agent."""
    document = also_on(
        merged(security=("full", "full"), ask=("off", "off")),
        "automazioni",
        security=("full", "deny"),
        ask=("off", "always"),
    )

    outcomes = check_agents(config_with(on_node(security="full", ask="off")), answer(document))

    assert by_id(outcomes)["openclaw.nodes.exec.capo"].severity == Severity.OK


def test_the_shared_exec_block_names_the_node_when_the_agent_does_not():
    config = config_with()
    config["tools"]["exec"] = {"host": "node", "node": "win-pc", "security": "full", "ask": "off"}

    outcomes = check_agents(config, answer(merged(security=("full", "allowlist"))))

    assert by_id(outcomes)["openclaw.nodes.exec.capo"].severity == Severity.WARN


def test_a_merge_the_node_did_not_compute_is_not_judged():
    """An older node reports the effective policy as unavailable instead of assuming the gateway's."""
    config = config_with(on_node())
    names: list[str] = []
    unmerged = {"path": "C:\\...\\openclaw.sqlite", "exists": True, "file": {"version": 1}}

    outcomes = check_agents(config, lambda node: names.append(node) or Reading(document=unmerged))

    assert outcomes == []
    assert names == ["win-pc"]


# --------------------------------------------------------------- who is asked


def test_an_agent_that_runs_on_the_gateway_is_never_asked_about():
    names: list[str] = []

    outcomes = check_agents(
        config_with({"host": "gateway", "node": "win-pc"}),
        lambda node: names.append(node) or Reading(document=merged()),
    )

    assert outcomes == []
    assert names == []


def test_an_agent_that_names_no_node_is_not_asked():
    """OpenClaw picks the sole eligible node; this check has no node list to resolve that with."""
    names: list[str] = []

    outcomes = check_agents(
        config_with({"host": "node"}),
        lambda node: names.append(node) or Reading(document=merged()),
    )

    assert outcomes == []
    assert names == []


def test_without_a_reader_nothing_is_asked():
    """`nexgen doctor` runs on a schedule: it must not start OpenClaw's CLI."""
    assert check_agents(config_with(on_node()), None) == []


def test_two_agents_on_the_same_node_cost_one_query():
    config = config_with(on_node(security="full", ask="off"))
    config["agents"]["entries"]["automazioni"] = {"tools": {"exec": {"host": "node", "node": "win-pc"}}}

    names: list[str] = []
    check_agents(config, lambda node: names.append(node) or Reading(document=merged()))

    assert names == ["win-pc"]


# ------------------------------------------------------------- what can go wrong


def test_a_node_that_cannot_be_read_is_a_warning_that_names_the_command():
    outcomes = check_agents(
        config_with(on_node()),
        lambda node: Reading(reason="gateway timeout after 10000ms"),
    )

    outcome = by_id(outcomes)["openclaw.nodes.exec.capo"]
    assert outcome.severity == Severity.WARN
    assert "gateway timeout after 10000ms" in outcome.message
    assert "openclaw approvals get --node win-pc" in (outcome.action or "")


def test_a_host_native_node_that_denies_is_named_with_its_own_body():
    """A Windows or macOS companion enforces its own policy: OpenClaw cannot write it from here."""
    outcomes = check_agents(
        config_with(on_node()),
        answer({"enabled": True, "defaultAction": "deny", "rules": [{"pattern": "hostname", "action": "allow"}]}),
    )

    outcome = by_id(outcomes)["openclaw.nodes.exec.capo"]
    assert outcome.severity == Severity.WARN
    assert "its own exec policy (deny)" in outcome.message
    assert node_approvals.NATIVE_ALLOW_BODY in (outcome.action or "")


def test_a_host_native_node_that_allows_commands_is_not_a_warning():
    outcomes = check_agents(
        config_with(on_node()),
        answer({"enabled": True, "defaultAction": "allow", "rules": []}),
    )

    assert by_id(outcomes)["openclaw.nodes.exec.capo"].severity == Severity.OK


def test_a_host_native_node_with_its_policy_disabled_says_it_cannot_be_configured():
    outcomes = check_agents(
        config_with(on_node()),
        answer({"enabled": False, "hash": "abc", "message": "policy disabled"}),
    )

    outcome = by_id(outcomes)["openclaw.nodes.exec.capo"]
    assert outcome.severity == Severity.WARN
    assert "disabled" in outcome.message


def test_a_value_openclaw_would_refuse_is_never_read_as_a_policy():
    outcomes = check_agents(
        config_with(on_node(security="full", ask="off")),
        answer(merged(security=("full", "sometimes"), ask=("off", "maybe"))),
    )

    assert by_id(outcomes)["openclaw.nodes.exec.capo"].severity == Severity.OK


# ------------------------------------------------------------------ through the doctor


def machine(config_path: Path) -> Detection:
    return Detection(
        openclaw=Installation(installed=True, version=PINNED, config_path=str(config_path), config_present=True),
        claude=Login(binary=False, login=None),
        codex=Login(binary=False, login=None),
        opencode_go=True,
        local=LocalModels(ollama=()),
        n8n=False,
    )


def run_doctor(tmp_path: Path, config: dict, reader=None):
    config_path = tmp_path / "home" / ".openclaw" / "openclaw.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config), encoding="utf-8")
    vault = tmp_path / "vault" / "03-INFRA" / "agent-universal-layer"
    vault.mkdir(parents=True, exist_ok=True)
    (vault / "modules.state.yaml").write_text("schema_version: 2\nmodules:\n  openclaw: local\n", encoding="utf-8")
    return collect(
        detected=machine(config_path),
        home=tmp_path / "home",
        vault_data=tmp_path / "vault",
        engine_root=tmp_path / "engine",
        env={"LOCALAPPDATA": str(tmp_path / "localappdata"), "APPDATA": str(tmp_path / "appdata")},
        platform="linux",
        connect=lambda host, port, timeout: False,
        node_approvals_reader=reader,
        node_version=lambda: "v24.22.1",
        linger_dir=tmp_path / "linger",
    )


def test_the_doctor_carries_the_line_when_it_was_asked_for(tmp_path):
    outcomes = by_id(run_doctor(
        tmp_path,
        config_with({"host": "node", "node": "win-pc", "security": "full", "ask": "off"}),
        lambda node: Reading(document=merged(security=("full", "allowlist"), ask=("off", "on-miss"))),
    ))

    assert outcomes["openclaw.nodes.exec.capo"].severity == Severity.WARN


def test_the_unscheduled_doctor_asks_no_node_anything(tmp_path):
    outcomes = by_id(run_doctor(
        tmp_path,
        config_with({"host": "node", "node": "win-pc", "security": "full", "ask": "off"}),
        None,
    ))

    assert not any(key.startswith("openclaw.nodes.exec.") for key in outcomes)


def test_the_nodes_flag_turns_the_approvals_query_on(monkeypatch):
    """`--nodes` is the one flag that lets the doctor start OpenClaw's CLI, for both node questions."""
    captured: dict = {}

    def fake_collect(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr("nexgen_core.openclaw.doctor.collect", fake_collect)
    from nexgen_core.cli import openclaw_cmds

    assert openclaw_cmds.cmd_doctor(argparse.Namespace(json=False, nodes=True)) == 0
    assert captured["node_approvals_reader"] is node_approvals.default_reader

    captured.clear()
    assert openclaw_cmds.cmd_doctor(argparse.Namespace(json=False, nodes=False)) == 0
    assert captured["node_approvals_reader"] is None
