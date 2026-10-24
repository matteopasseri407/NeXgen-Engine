"""What OpenClaw created and the library does not know about yet: the read-only scan.

Every test builds its own state directory and its own `openclaw.json` under
`tmp_path`. Nothing here reads a real state directory, and nothing here writes:
the scan is the question the doctor and the command both ask.
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
from nexgen_core.openclaw.adopt_scan import (  # noqa: E402 - sys.path shim for cloned checkout
    declared_names,
    live_servers,
    scan,
    unmanaged_servers,
    workshop_skills,
)


@pytest.fixture(autouse=True)
def _english():
    i18n.set_language("en")
    yield
    i18n.set_language(None)


def a_learned_skill(state: Path, agent: str, name: str, *, agent_dir: Path | None = None) -> Path:
    base = (agent_dir or state / "agents" / agent / "agent") / "workshop-skills"
    skill = base / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: what {name} does\n---\n\n# {name}\n", encoding="utf-8",
    )
    return skill


def a_config(*agent_ids: str) -> dict:
    return {"agents": {"entries": {agent_id: {} for agent_id in agent_ids}}}


def a_manifest(*servers: str, retired: list[str] | None = None) -> dict:
    declared = {"servers": {name: {"exposure": "lazy", "targets": ["openclaw"]} for name in servers}}
    if retired is not None:
        declared["retired_servers"] = retired
    return declared


# ---------------------------------------------------------------- learned skills


def test_a_learned_skill_the_manifest_does_not_declare_is_reported(tmp_path):
    state = tmp_path / "state"
    a_learned_skill(state, "main", "release-notes")

    found = workshop_skills(a_config("main"), state, tmp_path / "home", declared=set())

    assert [(skill.name, skill.agent_id) for skill in found] == [("release-notes", "main")]
    assert found[0].path == state / "agents" / "main" / "agent" / "workshop-skills" / "release-notes"


def test_a_learned_skill_the_manifest_already_declares_is_not_reported(tmp_path):
    state = tmp_path / "state"
    a_learned_skill(state, "main", "release-notes")

    found = workshop_skills(a_config("main"), state, tmp_path / "home", declared={"release-notes"})

    assert found == []


def test_the_archive_is_history_and_never_a_live_skill(tmp_path):
    state = tmp_path / "state"
    archived = state / "agents" / "main" / "agent" / "workshop-skills" / ".archive" / "old"
    archived.mkdir(parents=True)

    assert workshop_skills(a_config("main"), state, tmp_path / "home", declared=set()) == []


def test_a_custom_agent_dir_is_where_its_workshop_skills_are(tmp_path):
    state = tmp_path / "state"
    elsewhere = tmp_path / "elsewhere"
    a_learned_skill(state, "main", "kept-elsewhere", agent_dir=elsewhere)
    config = {"agents": {"entries": {"main": {"agentDir": str(elsewhere)}}}}

    found = workshop_skills(config, state, tmp_path / "home", declared=set())

    assert [skill.path for skill in found] == [elsewhere / "workshop-skills" / "kept-elsewhere"]


def test_two_agents_each_report_their_own_skills(tmp_path):
    state = tmp_path / "state"
    a_learned_skill(state, "main", "from-main")
    a_learned_skill(state, "work", "from-work")

    found = workshop_skills(a_config("main", "work"), state, tmp_path / "home", declared=set())

    assert sorted((skill.agent_id, skill.name) for skill in found) == [
        ("main", "from-main"), ("work", "from-work"),
    ]


def test_a_missing_workshop_folder_is_not_an_error(tmp_path):
    found = workshop_skills(a_config("main"), tmp_path / "state", tmp_path / "home", declared=set())

    assert found == []


def test_a_config_without_agents_has_no_workshop(tmp_path):
    assert workshop_skills({}, tmp_path / "state", tmp_path / "home", declared=set()) == []
    assert workshop_skills(None, tmp_path / "state", tmp_path / "home", declared=set()) == []


# ------------------------------------------------------------------ live servers


def test_a_server_the_manifest_declares_is_engine_owned():
    found = unmanaged_servers({"declared": {"url": "https://x"}}, a_manifest("declared"))

    assert found == []


def test_a_retired_server_does_not_come_back_in_through_adoption():
    found = unmanaged_servers({"gone": {"url": "https://x"}}, a_manifest(retired=["gone"]))

    assert found == []


def test_a_server_nobody_declared_is_the_one_to_adopt():
    found = unmanaged_servers({"added-by-hand": {"command": "npx", "args": ["-y", "pkg"]}}, a_manifest("known"))

    assert [(server.name, server.spec["command"]) for server in found] == [("added-by-hand", "npx")]


def test_servers_come_back_sorted_by_name():
    live = {"zeta": {}, "alpha": {}}

    assert [server.name for server in unmanaged_servers(live, {})] == ["alpha", "zeta"]


def test_a_spec_that_is_not_an_object_is_still_reported_rather_than_crashing():
    found = unmanaged_servers({"odd": "just-a-string"}, {})

    assert found[0].spec == {}


def test_the_live_table_tolerates_a_file_without_mcp():
    assert live_servers({"gateway": {}}) == {}
    assert live_servers({}) == {}
    assert live_servers(None) == {}
    assert live_servers({"mcp": {"servers": {"a": {}}}}) == {"a": {}}


def test_declared_names_holds_the_living_and_the_retired():
    assert declared_names(a_manifest("alive", retired=["dead"])) == {"alive", "dead"}
    assert declared_names(None) == set()


# ------------------------------------------------------------------------ scan


def test_the_scan_asks_the_file_once_and_reports_both_kinds(tmp_path):
    state = tmp_path / "state"
    home = tmp_path / "home"
    a_learned_skill(state, "main", "learned")
    config_path = state / "openclaw.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps({
        "agents": {"entries": {"main": {}}},
        "mcp": {"servers": {"posted-by-hand": {"url": "https://example.test/mcp"}}},
    }), encoding="utf-8")

    found = scan(
        config_path=config_path, state_dir=state, home=home,
        skills_manifest={"skills": {}}, mcp_manifest=a_manifest("engine-owned"),
    )

    assert [skill.name for skill in found.skills] == ["learned"]
    assert [server.name for server in found.servers] == ["posted-by-hand"]
    assert found.total == 2
    assert found.notes == ()


def test_a_machine_without_openclaw_json_has_nothing_to_adopt(tmp_path):
    found = scan(
        config_path=tmp_path / "state" / "openclaw.json", state_dir=tmp_path / "state", home=tmp_path / "home",
        skills_manifest={"skills": {}}, mcp_manifest={"servers": {}},
    )

    assert found.total == 0
    assert found.notes == ()


def test_a_broken_file_says_so_instead_of_pretending_there_is_nothing(tmp_path):
    config_path = tmp_path / "state" / "openclaw.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("{not json", encoding="utf-8")

    found = scan(
        config_path=config_path, state_dir=tmp_path / "state", home=tmp_path / "home",
        skills_manifest={"skills": {}}, mcp_manifest={"servers": {}},
    )

    assert found.total == 0
    assert found.notes and "not readable" in found.notes[0]
