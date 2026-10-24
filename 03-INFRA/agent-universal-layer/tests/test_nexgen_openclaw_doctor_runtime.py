"""The live-state checks: learned skills per agent and the Companion's own gateway.

Every test builds its own folders under `tmp_path` and injects the WSL list:
nothing here reads the real home, the real registry, or the real settings. The
Companion section is about words as much as severities: the app is never
something to remove, and only two gateways claiming the same PC is a conflict.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import i18n  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.companion import Companion, probe  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.doctor_runtime import (  # noqa: E402 - sys.path shim for cloned checkout
    check_companion_gateway,
    check_unadopted,
    check_workshop_skills,
)
from nexgen_core.report import Severity  # noqa: E402 - sys.path shim for cloned checkout

NOW = 1_800_000_000.0
OLD = NOW - 3 * 24 * 60 * 60


@pytest.fixture(autouse=True)
def _english():
    i18n.set_language("en")
    yield
    i18n.set_language(None)


def an_agent_config(*agent_ids: str, agent_dir: str | None = None) -> dict:
    entries = {}
    for agent_id in agent_ids:
        entries[agent_id] = {"agentDir": agent_dir} if agent_dir else {}
    return {"agents": {"entries": entries}}


def a_skill(directory: Path, name: str, mtime: float) -> Path:
    skill = directory / name
    skill.mkdir(parents=True)
    body = skill / "SKILL.md"
    body.write_text("# learned\n", encoding="utf-8")
    os.utime(body, (mtime, mtime))
    os.utime(skill, (mtime, mtime))
    return skill


# ------------------------------------------------------------------ learned skills


def test_an_agent_with_no_workshop_folder_reports_zero(tmp_path):
    outcomes = check_workshop_skills(an_agent_config("main"), tmp_path / "state", tmp_path / "home", now=NOW)

    assert [o.id for o in outcomes] == ["openclaw.workshop_skills.main"]
    assert outcomes[0].severity is Severity.OK
    assert "0 learned skills" in outcomes[0].message


def test_only_the_skills_changed_in_the_last_24_hours_are_counted(tmp_path):
    workshop = tmp_path / "state" / "agents" / "main" / "agent" / "workshop-skills"
    a_skill(workshop, "fresh", NOW - 60)
    a_skill(workshop, "old", OLD)

    outcomes = check_workshop_skills(an_agent_config("main"), tmp_path / "state", tmp_path / "home", now=NOW)

    assert "1 learned skills" in outcomes[0].message


def test_more_than_ten_recent_skills_is_a_warning(tmp_path):
    workshop = tmp_path / "state" / "agents" / "main" / "agent" / "workshop-skills"
    for index in range(11):
        a_skill(workshop, f"skill-{index}", NOW - 60)

    outcome = check_workshop_skills(an_agent_config("main"), tmp_path / "state", tmp_path / "home", now=NOW)[0]

    assert outcome.severity is Severity.WARN
    assert outcome.action and "Review" in outcome.action


def test_an_explicit_agent_dir_replaces_the_default_folder(tmp_path):
    agent_dir = tmp_path / "custom-agent"
    a_skill(agent_dir / "workshop-skills", "fresh", NOW - 60)

    outcome = check_workshop_skills(
        an_agent_config("main", agent_dir=str(agent_dir)), tmp_path / "state", tmp_path / "home", now=NOW,
    )[0]

    assert "1 learned skills" in outcome.message


def test_every_agent_gets_its_own_line(tmp_path):
    workshop = tmp_path / "state" / "agents" / "capo" / "agent" / "workshop-skills"
    a_skill(workshop, "fresh", NOW - 60)

    outcomes = check_workshop_skills(an_agent_config("main", "capo"), tmp_path / "state", tmp_path / "home", now=NOW)

    assert {o.id for o in outcomes} == {"openclaw.workshop_skills.main", "openclaw.workshop_skills.capo"}
    counts = {o.id: o.message for o in outcomes}
    assert "0 learned skills" in counts["openclaw.workshop_skills.main"]
    assert "1 learned skills" in counts["openclaw.workshop_skills.capo"]


# ------------------------------------------------------------------ unadopted

def a_library(tmp_path: Path, *, skills: str = "skills: {}", servers: str = "servers: {}") -> Path:
    """The two manifests the engine reads, in the shape a Vault keeps them."""
    vault = tmp_path / "vault"
    skills_dir = vault / "03-INFRA" / "agent-universal-layer" / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "skills.manifest.yaml").write_text(f"schema_version: 1\n{skills}\n", encoding="utf-8")
    mcp_dir = vault / "03-INFRA" / "agent-universal-layer" / "mcp"
    mcp_dir.mkdir(parents=True)
    (mcp_dir / "manifest.yaml").write_text(f"schema_version: 1\n{servers}\n", encoding="utf-8")
    return vault


def a_hand_added_server_config() -> dict:
    return {**an_agent_config("main"), "mcp": {"servers": {
        "hand-added": {"command": "npx", "args": ["-y", "some-mcp"]},
    }}}


def test_a_library_that_knows_everything_is_a_pass(tmp_path):
    outcome = check_unadopted(
        an_agent_config("main"), tmp_path / "state" / "openclaw.json", tmp_path / "home", a_library(tmp_path),
    )

    assert outcome is not None
    assert outcome.severity is Severity.OK
    assert outcome.action is None


def test_a_learned_skill_and_a_hand_added_server_are_both_counted(tmp_path):
    a_skill(tmp_path / "state" / "agents" / "main" / "agent" / "workshop-skills", "fresh", NOW - 60)

    outcome = check_unadopted(
        a_hand_added_server_config(), tmp_path / "state" / "openclaw.json", tmp_path / "home",
        a_library(tmp_path),
    )

    assert outcome is not None and outcome.severity is Severity.WARN
    assert "2 resources created in OpenClaw are not adopted yet" in outcome.message
    assert "nexgen openclaw adopt" in outcome.action


def test_what_the_manifests_already_declare_is_not_counted(tmp_path):
    a_skill(tmp_path / "state" / "agents" / "main" / "agent" / "workshop-skills", "fresh", NOW - 60)
    vault = a_library(
        tmp_path,
        skills="skills:\n  fresh:\n    origin: vault",
        servers="servers:\n  hand-added:\n    transport: stdio\n    command: npx",
    )

    outcome = check_unadopted(
        a_hand_added_server_config(), tmp_path / "state" / "openclaw.json", tmp_path / "home", vault,
    )

    assert outcome is not None and outcome.severity is Severity.OK


def test_a_manifest_that_cannot_be_read_makes_the_check_silent(tmp_path):
    vault = a_library(tmp_path)
    (vault / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml").write_text(
        "servers: not-a-map\n", encoding="utf-8",
    )

    assert check_unadopted(
        a_hand_added_server_config(), tmp_path / "state" / "openclaw.json", tmp_path / "home", vault,
    ) is None


# ----------------------------------------------------------------- the Companion


def companion_env(tmp_path: Path) -> dict:
    return {
        "LOCALAPPDATA": str(tmp_path / "local"),
        "APPDATA": str(tmp_path / "roaming"),
    }


def app_gateway(tmp_path: Path, distros: list[str] | None = None) -> Companion:
    """The app's gateway as the probe would return it, on a fake home."""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    return probe(home, companion_env(tmp_path), "win32", wsl_distributions=lambda: list(distros or []))


def test_a_machine_without_the_companion_says_nothing(tmp_path):
    assert check_companion_gateway(Companion(), True, "win32") is None


def test_the_app_owned_wsl_distribution_is_a_warning(tmp_path):
    outcome = check_companion_gateway(app_gateway(tmp_path, ["Ubuntu", "OpenClawGateway"]), True, "win32")

    assert outcome is not None and outcome.severity is Severity.WARN
    assert "OpenClawGateway" in outcome.message
    assert "Connections" in (outcome.action or "")


def test_the_app_wsl_storage_folder_is_a_warning(tmp_path):
    (Path(companion_env(tmp_path)["LOCALAPPDATA"]) / "OpenClawTray" / "wsl").mkdir(parents=True)

    outcome = check_companion_gateway(app_gateway(tmp_path), True, "win32")

    assert outcome is not None and outcome.severity is Severity.WARN


def test_a_companion_gateway_url_on_another_port_is_a_warning(tmp_path):
    settings = Path(companion_env(tmp_path)["APPDATA"]) / "OpenClawTray" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({
        "gatewayUrl": "ws://localhost:9999/?token=super-secret-token",
    }), encoding="utf-8")

    outcome = check_companion_gateway(app_gateway(tmp_path), True, "win32")

    assert outcome is not None and outcome.severity is Severity.WARN
    assert "super-secret-token" not in outcome.message, "a URL can carry a token: never print it"


def test_a_companion_pointing_at_this_gateway_is_a_line_that_says_so(tmp_path):
    settings = Path(companion_env(tmp_path)["APPDATA"]) / "OpenClawTray" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"gatewayUrl": "ws://localhost:18789"}), encoding="utf-8")

    outcome = check_companion_gateway(app_gateway(tmp_path), True, "win32")

    assert outcome is not None and outcome.severity is Severity.OK
    assert "set to this PC's gateway on port 18789" in outcome.message


def test_the_app_on_this_gateway_says_whether_its_device_is_listed(tmp_path):
    settings = Path(companion_env(tmp_path)["APPDATA"]) / "OpenClawTray" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"gatewayUrl": "ws://localhost:18789"}), encoding="utf-8")

    outcome = check_companion_gateway(
        app_gateway(tmp_path), True, "win32",
        pending=lambda: [{"id": "req-1", "summary": "OpenClaw Companion on Windows", "status": "pending"}],
    )

    assert "The gateway lists its device." in outcome.message


def test_an_app_whose_device_the_gateway_does_not_list_is_told_the_step(tmp_path):
    settings = Path(companion_env(tmp_path)["APPDATA"]) / "OpenClawTray" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"gatewayUrl": "ws://localhost:18789"}), encoding="utf-8")

    outcome = check_companion_gateway(app_gateway(tmp_path), True, "win32", pending=lambda: [])

    assert "lists no device for it yet" in outcome.message
    assert "nexgen openclaw pair" in outcome.message


def test_a_gateway_that_cannot_be_asked_says_nothing_about_the_device(tmp_path):
    settings = Path(companion_env(tmp_path)["APPDATA"]) / "OpenClawTray" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"gatewayUrl": "ws://localhost:18789"}), encoding="utf-8")

    outcome = check_companion_gateway(app_gateway(tmp_path), True, "win32", pending=lambda: None)

    assert outcome.message.endswith("on port 18789."), "a stopped gateway is not a fault about the app"


def test_an_installed_app_that_names_no_gateway_gets_the_step_it_needs(tmp_path):
    settings = Path(companion_env(tmp_path)["APPDATA"]) / "OpenClawTray" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"docsUrl": "https://openclaw.ai/docs"}), encoding="utf-8")

    outcome = check_companion_gateway(app_gateway(tmp_path), True, "win32")

    assert outcome is not None and outcome.severity is Severity.OK
    assert "names no gateway on this PC" in outcome.message
    assert "a local Gateway on this PC" in (outcome.action or "")


def test_the_companion_check_is_windows_only(tmp_path):
    assert check_companion_gateway(
        Companion(present=True, owns_local_gateway=True), True, "linux",
    ) is None


def test_the_app_alone_is_one_informative_line(tmp_path):
    """Case (b): the app runs the only gateway, so there is nothing to conflict with."""
    outcome = check_companion_gateway(app_gateway(tmp_path, ["OpenClawGateway"]), False, "win32")

    assert outcome is not None and outcome.severity is Severity.OK
    assert "not the app's one" in outcome.message
    assert "conflict" not in outcome.message.casefold()
    assert "as it is" in (outcome.action or "")


def test_a_remote_app_is_informative_and_says_so(tmp_path):
    """Case (d): the app talks to a gateway elsewhere, which no native install can contradict."""
    settings = Path(companion_env(tmp_path)["APPDATA"]) / "OpenClawTray" / "gateways.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"vps": {"url": "wss://gateway.example.com"}}), encoding="utf-8")

    outcome = check_companion_gateway(app_gateway(tmp_path), True, "win32")

    assert outcome is not None and outcome.severity is Severity.OK
    assert "elsewhere" in outcome.message


def test_the_line_never_tells_the_person_to_remove_the_app(tmp_path):
    """The app is a gateway the person chose: the engine says what it writes, not what to uninstall."""
    both = check_companion_gateway(app_gateway(tmp_path, ["OpenClawGateway"]), True, "win32")
    alone = check_companion_gateway(app_gateway(tmp_path, ["OpenClawGateway"]), False, "win32")

    for outcome in (both, alone):
        words = f"{outcome.message} {outcome.action or ''}".casefold()
        assert "remove the app" not in words
        assert "uninstall the app" not in words
    assert "npm uninstall -g openclaw" in (both.action or ""), "the native one is the engine's to give up"

