"""The live-state checks: learned skills per agent and the Companion's own gateway.

Every test builds its own folders under `tmp_path` and injects the WSL list:
nothing here reads the real home, the real registry, or the real settings.
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
from nexgen_core.openclaw.doctor_runtime import (  # noqa: E402 - sys.path shim for cloned checkout
    check_companion_gateway,
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


# ----------------------------------------------------------------- the Companion


def companion_env(tmp_path: Path) -> dict:
    return {
        "LOCALAPPDATA": str(tmp_path / "local"),
        "APPDATA": str(tmp_path / "roaming"),
    }


def test_a_machine_without_the_companion_says_nothing(tmp_path):
    assert check_companion_gateway(
        tmp_path / "home", companion_env(tmp_path), "win32", wsl_distributions=lambda: [],
    ) is None


def test_the_app_owned_wsl_distribution_is_a_warning(tmp_path):
    outcome = check_companion_gateway(
        tmp_path / "home", companion_env(tmp_path), "win32",
        wsl_distributions=lambda: ["Ubuntu", "OpenClawGateway"],
    )

    assert outcome is not None and outcome.severity is Severity.WARN
    assert "OpenClawGateway" in outcome.message
    assert "local Gateway on this PC" in (outcome.action or "")


def test_the_app_wsl_storage_folder_is_a_warning(tmp_path):
    env = companion_env(tmp_path)
    (Path(env["LOCALAPPDATA"]) / "OpenClawTray" / "wsl").mkdir(parents=True)

    outcome = check_companion_gateway(
        tmp_path / "home", env, "win32", wsl_distributions=lambda: [],
    )

    assert outcome is not None and outcome.severity is Severity.WARN


def test_a_companion_gateway_url_on_another_port_is_a_warning(tmp_path):
    env = companion_env(tmp_path)
    settings = Path(env["APPDATA"]) / "OpenClawTray" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({
        "gatewayUrl": "ws://localhost:9999/?token=super-secret-token",
    }), encoding="utf-8")

    outcome = check_companion_gateway(
        tmp_path / "home", env, "win32", wsl_distributions=lambda: [],
    )

    assert outcome is not None and outcome.severity is Severity.WARN
    assert "super-secret-token" not in outcome.message, "a URL can carry a token: never print it"


def test_a_companion_pointing_at_this_gateway_is_not_a_warning(tmp_path):
    env = companion_env(tmp_path)
    settings = Path(env["APPDATA"]) / "OpenClawTray" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"gatewayUrl": "ws://localhost:18789"}), encoding="utf-8")

    assert check_companion_gateway(
        tmp_path / "home", env, "win32", wsl_distributions=lambda: [],
    ) is None


def test_the_companion_check_is_windows_only(tmp_path):
    assert check_companion_gateway(
        tmp_path / "home", companion_env(tmp_path), "linux", wsl_distributions=lambda: ["OpenClawGateway"],
    ) is None
