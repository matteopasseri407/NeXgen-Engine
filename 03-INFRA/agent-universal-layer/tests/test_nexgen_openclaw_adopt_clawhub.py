"""The skills ClawHub installs and the ones a plugin publishes: found, and adopted.

OpenClaw writes a skill into three folders: the Workshop it learns into, the
agent workspace that `openclaw skills install` (and so ClawHub) fills, and the
`plugin-skills` folder it fills with a link per plugin skill. Only the Workshop
copy can be archived after an adoption, so the scan has to say which folder a
candidate came from.

Every folder here is built under `tmp_path` and every link is one this test
made: nothing reads a real OpenClaw state directory, and no `openclaw` command
runs.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import i18n  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.adopt_scan import (  # noqa: E402
    PLUGIN,
    WORKSHOP,
    WORKSPACE,
    WorkshopSkill,
    plugin_skills,
    scan,
    workspace_skills,
    workshop_skills,
)
from nexgen_core.openclaw.adopt_skills import adopt_skill, vault_skills_dir  # noqa: E402
from nexgen_core.openclaw_processes import RunResult  # noqa: E402


@pytest.fixture(autouse=True)
def _english():
    i18n.set_language("en")
    yield
    i18n.set_language(None)


def a_skill(folder: Path, name: str) -> Path:
    """One skill folder, shaped the way OpenClaw loads it."""
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: what {name} does\n---\n\n# {name}\n", encoding="utf-8",
    )
    return folder


def a_workspace(home: Path, agent: str = "main") -> Path:
    """The workspace OpenClaw gives an agent that pins none: `<state>/workspace`."""
    return home / ".openclaw" / ("workspace" if agent == "main" else f"workspace-{agent}")


def a_config(workspace: Path | None = None, *agent_ids: str) -> dict:
    """`openclaw.json` with the agents the scan reads, and their workspaces."""
    entries = {agent_id: {} for agent_id in agent_ids}
    if workspace is not None:
        entries = {agent_id: {"workspace": str(workspace)} for agent_id in entries}
    return {"agents": {"entries": entries}}


def link_dir(target: Path, link: Path) -> None:
    """The link OpenClaw generates: a symlink, or a junction where Windows wants one."""
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except (OSError, NotImplementedError) as exc:
        if sys.platform != "win32":
            raise AssertionError(f"this filesystem cannot hold a directory link: {exc}") from exc
    made = subprocess.run(  # noqa: S603 - the junction fallback, on Windows only
        ["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True,
    )
    assert made.returncode == 0, f"this session cannot create a directory link: {made.stderr.strip()}"


# ------------------------------------------------------------- the ClawHub folder


def test_a_skill_clawhub_installed_is_found(tmp_path):
    """`openclaw skills install <slug>` copies into `<workspace>/skills/<slug>`."""
    state, home = tmp_path / "state", tmp_path / "home"
    workspace = a_workspace(home)
    a_skill(workspace / "skills" / "release-notes", "release-notes")

    found = workspace_skills(a_config(workspace, "main"), state, home, declared=set())

    assert [(skill.name, skill.source) for skill in found] == [("release-notes", WORKSPACE)]
    assert found[0].path == workspace / "skills" / "release-notes"


def test_a_workspace_pinned_in_the_defaults_is_read_for_every_agent(tmp_path):
    state, home = tmp_path / "state", tmp_path / "home"
    root = tmp_path / "shared"
    a_skill(root / "skills" / "from-defaults", "from-defaults")
    config = {"agents": {"defaults": {"workspace": str(root)}, "entries": {"main": {}}}}

    found = workspace_skills(config, state, home, declared=set())

    assert [skill.path for skill in found] == [root / "skills" / "from-defaults"]


def test_the_archive_of_a_workspace_is_history_and_not_a_skill(tmp_path):
    state, home = tmp_path / "state", tmp_path / "home"
    workspace = a_workspace(home)
    a_skill(workspace / "skills" / ".archive" / "old", "old")

    assert workspace_skills(a_config(workspace, "main"), state, home, declared=set()) == []


def test_the_skill_files_the_engine_writes_in_a_workspace_are_not_skills(tmp_path):
    """`setup_skills` puts `<name>.md` and `INDICE.md` beside the folders, not in them."""
    state, home = tmp_path / "state", tmp_path / "home"
    workspace = a_workspace(home)
    folder = workspace / "skills"
    a_skill(folder / "from-clawhub", "from-clawhub")
    (folder / "vault-save.md").write_text("<!-- nexgen:squad-skill -->\nbody\n", encoding="utf-8")
    (folder / "INDICE.md").write_text("<!-- nexgen:squad-skill -->\n# Skill\n", encoding="utf-8")

    found = workspace_skills(a_config(workspace, "main"), state, home, declared=set())

    assert [skill.name for skill in found] == ["from-clawhub"]


def test_a_workspace_skill_the_manifest_declares_is_not_reported_again(tmp_path):
    state, home = tmp_path / "state", tmp_path / "home"
    workspace = a_workspace(home)
    a_skill(workspace / "skills" / "already-mine", "already-mine")

    found = workspace_skills(a_config(workspace, "main"), state, home, declared={"already-mine"})

    assert found == []


# -------------------------------------------------------------- the Workshop copy


def test_a_workshop_skill_is_still_found_as_before(tmp_path):
    state, home = tmp_path / "state", tmp_path / "home"
    a_skill(state / "agents" / "main" / "agent" / "workshop-skills" / "learned", "learned")

    found = workshop_skills(a_config(None, "main"), state, home, declared=set())

    assert [(skill.name, skill.source) for skill in found] == [("learned", WORKSHOP)]


# --------------------------------------------------------------- the plugin skills


def a_plugin_skill(root: Path, plugin: str, name: str) -> Path:
    """The folder a plugin publishes its skill from."""
    return a_skill(root / plugin / "skills" / name, name)


def a_plugin_manifest(folder: Path, *, plugin_id: str | None = None, package: str | None = None) -> None:
    """The two files OpenClaw reads a plugin's names from."""
    folder.mkdir(parents=True, exist_ok=True)
    if plugin_id is not None:
        (folder / "openclaw.plugin.json").write_text(json.dumps({"id": plugin_id}), encoding="utf-8")
    if package is not None:
        (folder / "package.json").write_text(json.dumps({"name": package}), encoding="utf-8")


def an_official_catalog(state: Path, *entries: dict) -> None:
    """OpenClaw's own catalog snapshot, the table `read_catalog_snapshots` reads."""
    import sqlite3

    state.mkdir(parents=True, exist_ok=True)
    db = state / "openclaw.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE official_external_plugin_catalog_snapshots (body TEXT)")
        conn.execute(
            "INSERT INTO official_external_plugin_catalog_snapshots (body) VALUES (?)",
            (json.dumps({"entries": list(entries)}),),
        )


def test_a_skill_of_a_bundled_plugin_is_not_a_candidate(tmp_path):
    """A plugin inside the package publishes from inside the package: not the person's."""
    state = tmp_path / "state"
    package = tmp_path / "npm" / "node_modules" / "openclaw" / "dist" / "extensions"
    target = a_plugin_skill(package, "browser", "browser-automation")
    link_dir(target, state / "plugin-skills" / "browser-automation")

    found, notes = plugin_skills(state, declared=set())

    assert found == []
    assert notes == []


def test_a_skill_of_a_plugin_in_the_official_catalog_is_not_a_candidate(tmp_path):
    """OpenClaw installs its own catalogue plugins under extensions too."""
    state = tmp_path / "state"
    target = a_plugin_skill(state / "extensions", "acpx", "acp-router")
    a_plugin_manifest(state / "extensions" / "acpx", plugin_id="acpx", package="@openclaw/acpx")
    link_dir(target, state / "plugin-skills" / "acp-router")
    an_official_catalog(state, {"name": "@openclaw/acpx", "openclaw": {"plugin": {"id": "acpx"}}})

    found, _notes = plugin_skills(state, declared=set())

    assert found == []


def test_a_skill_of_the_engine_own_plugin_is_not_a_candidate(tmp_path):
    """The engine deposits its own plugin in the same folder: its skills are not the person's."""
    state = tmp_path / "state"
    target = a_plugin_skill(state / "extensions", "nexgen", "nexgen-doctor")
    a_plugin_manifest(state / "extensions" / "nexgen", plugin_id="nexgen", package="nexgen-openclaw-plugin")
    link_dir(target, state / "plugin-skills" / "nexgen-doctor")
    an_official_catalog(state, {"name": "@openclaw/acpx", "openclaw": {"plugin": {"id": "acpx"}}})

    found, _notes = plugin_skills(state, declared=set())

    assert found == []


def test_a_skill_of_a_third_party_plugin_is_a_candidate(tmp_path):
    state = tmp_path / "state"
    target = a_plugin_skill(state / "extensions", "my-tools", "my-skill")
    a_plugin_manifest(state / "extensions" / "my-tools", plugin_id="my-tools", package="my-tools")
    link_dir(target, state / "plugin-skills" / "my-skill")
    an_official_catalog(state, {"name": "@openclaw/acpx", "openclaw": {"plugin": {"id": "acpx"}}})

    found, notes = plugin_skills(state, declared=set())

    assert [(skill.name, skill.source) for skill in found] == [("my-skill", PLUGIN)]
    # The candidate is the folder itself, not the link: the copy reads real bytes.
    assert found[0].path == target.resolve()
    assert not found[0].path.is_symlink()
    assert notes == []


def test_an_unreadable_catalog_offers_no_plugin_skill_and_says_so(tmp_path):
    """Guessing here would either port OpenClaw's own skills or hide the person's."""
    state = tmp_path / "state"
    target = a_plugin_skill(state / "extensions", "my-tools", "my-skill")
    link_dir(target, state / "plugin-skills" / "my-skill")
    (state / "openclaw.sqlite").write_bytes(b"not a database at all")

    found, notes = plugin_skills(state, declared=set())

    assert found == []
    assert len(notes) == 1 and "catalog cannot be read" in notes[0]


def test_a_link_that_resolves_outside_the_installed_plugins_is_not_followed(tmp_path):
    state = tmp_path / "state"
    (state / "extensions").mkdir(parents=True)
    target = a_plugin_skill(tmp_path / "somewhere-else", "loose", "loose-skill")
    link_dir(target, state / "plugin-skills" / "loose-skill")
    an_official_catalog(state)

    found, _notes = plugin_skills(state, declared=set())

    assert found == []


def test_a_plugin_skill_the_manifest_declares_is_not_reported_again(tmp_path):
    state = tmp_path / "state"
    target = a_plugin_skill(state / "extensions", "my-tools", "my-skill")
    link_dir(target, state / "plugin-skills" / "my-skill")
    an_official_catalog(state)

    found, _notes = plugin_skills(state, declared={"my-skill"})

    assert found == []


def test_a_machine_without_an_installed_plugin_folder_has_no_plugin_candidates(tmp_path):
    assert plugin_skills(tmp_path / "state", declared=set()) == ([], [])


# ------------------------------------------------------------------------ the scan


def test_the_scan_reports_the_three_folders_and_one_candidate_per_name(tmp_path):
    state, home = tmp_path / "state", tmp_path / "home"
    workspace = a_workspace(home)
    a_skill(state / "agents" / "main" / "agent" / "workshop-skills" / "learned", "learned")
    a_skill(workspace / "skills" / "from-clawhub", "from-clawhub")
    # The same name in two folders: OpenClaw loads one copy, so the scan reports one.
    a_skill(workspace / "skills" / "learned", "learned")
    target = a_plugin_skill(state / "extensions", "my-tools", "my-skill")
    link_dir(target, state / "plugin-skills" / "my-skill")
    an_official_catalog(state, {"name": "@openclaw/acpx", "openclaw": {"plugin": {"id": "acpx"}}})
    config_path = state / "openclaw.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(a_config(workspace, "main")), encoding="utf-8")

    found = scan(
        config_path=config_path, state_dir=state, home=home,
        skills_manifest={"skills": {"kept": {}}}, mcp_manifest={"servers": {}},
    )

    assert [(skill.name, skill.source) for skill in found.skills] == [
        ("learned", WORKSHOP), ("from-clawhub", WORKSPACE), ("my-skill", PLUGIN),
    ]
    # The MCP side has its own note about that extension; no skill is left unexplained.
    assert not any("skill" in note.lower() for note in found.notes)


# -------------------------------------------------------------------- the adoption


@dataclass
class Machine:
    """The four folders an adoption reads and writes, all under `tmp_path`."""

    home: Path
    vault: Path
    engine: Path
    state: Path

    @property
    def manifest(self) -> Path:
        return vault_skills_dir(self.vault) / "skills.manifest.yaml"


@pytest.fixture
def a_machine(tmp_path) -> Machine:
    home = tmp_path / "home"
    home.mkdir()
    vault = tmp_path / "vault"
    skills = vault / "03-INFRA" / "agent-universal-layer" / "skills"
    skills.mkdir(parents=True)
    (skills / "skills.manifest.yaml").write_text(
        "schema_version: 1\nskills:\n  kept:\n    origin: vault\n    exposure: manual\n    targets: [claude]\n",
        encoding="utf-8",
    )
    (skills / "kept").mkdir()
    (skills / "kept" / "SKILL.md").write_text("---\nname: kept\ndescription: x\n---\n", encoding="utf-8")
    engine = tmp_path / "engine"
    (engine / "agent-universal-layer" / "skills").mkdir(parents=True)
    state = tmp_path / "state"
    state.mkdir()
    return Machine(home=home, vault=vault, engine=engine, state=state)


class Recorder:
    """The injected executor: records the argv and answers what a test needs."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> RunResult:
        self.calls.append(list(argv))
        return RunResult(returncode=0, stdout="", stderr="")


def test_adopting_a_clawhub_skill_runs_no_workshop_archive(a_machine, tmp_path):
    """The copy ClawHub installed is OpenClaw's own: nothing archives it, and nothing runs."""
    folder = a_skill(tmp_path / "workspace" / "skills" / "release-notes", "release-notes")
    skill = WorkshopSkill("release-notes", "main", folder, WORKSPACE)
    recorder = Recorder()

    outcome = adopt_skill(
        skill, "writes the release notes",
        home=a_machine.home, vault_data=a_machine.vault, engine_root=a_machine.engine,
        state_dir=a_machine.state, run=recorder, which=lambda name: "/fake/bin/openclaw",
    )

    assert outcome.adopted, outcome.note
    assert (vault_skills_dir(a_machine.vault) / "release-notes" / "SKILL.md").is_file()
    assert "  release-notes:\n    origin: vault\n" in a_machine.manifest.read_text(encoding="utf-8")
    assert (a_machine.home / ".agents" / "skills" / "release-notes" / "SKILL.md").is_file()
    assert recorder.calls == []
    assert "left in OpenClaw" in outcome.note
    # The installed copy is still there: the engine never removes what OpenClaw installed.
    assert (folder / "SKILL.md").is_file()


def test_adopting_a_plugin_skill_reaches_every_view_without_touching_the_plugin(a_machine, tmp_path):
    target = a_plugin_skill(tmp_path / "state" / "extensions", "my-tools", "my-skill")
    skill = WorkshopSkill("my-skill", "", target, PLUGIN)
    recorder = Recorder()

    outcome = adopt_skill(
        skill, "does what the person installed it for",
        home=a_machine.home, vault_data=a_machine.vault, engine_root=a_machine.engine,
        state_dir=a_machine.state, run=recorder, which=lambda name: "/fake/bin/openclaw",
    )

    assert outcome.adopted, outcome.note
    assert (vault_skills_dir(a_machine.vault) / "my-skill" / "SKILL.md").is_file()
    assert recorder.calls == []
    assert (target / "SKILL.md").is_file()
