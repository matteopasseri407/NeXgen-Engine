"""Adopting one learned skill: into the Vault, into the manifest, into every view.

The machine here is a set of temporary folders -- a home, a vault, an engine
layer and a state directory -- and the one external command an adoption runs
(the Workshop archive) goes through a recorder, never through a real `openclaw`.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import i18n  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.adopt_scan import WorkshopSkill  # noqa: E402
from nexgen_core.openclaw.adopt_skills import (  # noqa: E402
    ADOPTED_TARGETS,
    ARCHIVE_REASON,
    adopt_skill,
    append_entry,
    collision,
    vault_skills_dir,
)
from nexgen_core.openclaw_processes import RunResult  # noqa: E402


@pytest.fixture(autouse=True)
def _english():
    i18n.set_language("en")
    yield
    i18n.set_language(None)


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
    # A declared skill whose folder exists: the manifest must validate before and after.
    (skills / "kept").mkdir()
    (skills / "kept" / "SKILL.md").write_text("---\nname: kept\ndescription: x\n---\n", encoding="utf-8")
    engine = tmp_path / "engine"
    (engine / "agent-universal-layer" / "skills").mkdir(parents=True)
    state = tmp_path / "state"
    state.mkdir()
    return Machine(home=home, vault=vault, engine=engine, state=state)


def a_learned_skill(tmp_path: Path, name: str = "release-notes") -> WorkshopSkill:
    folder = tmp_path / "workshop" / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: writes the release notes\n---\n\n# {name}\n\nSteps.\n",
        encoding="utf-8",
    )
    return WorkshopSkill(name=name, agent_id="main", path=folder)


class Recorder:
    """The injected executor: records the argv and answers what a test needs."""

    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        self.calls: list[list[str]] = []
        self.returncode = returncode
        self.stderr = stderr

    def __call__(self, argv: list[str]) -> RunResult:
        self.calls.append(list(argv))
        return RunResult(returncode=self.returncode, stdout="", stderr=self.stderr)


def on_path(binary: str = "openclaw"):
    """A `which` that finds the binary, so no real PATH is consulted."""
    return lambda name: binary if name == "openclaw" else None


# ----------------------------------------------------------------- the manifest


def test_a_free_name_has_no_collision(a_machine):
    assert collision(a_machine.manifest.read_text(encoding="utf-8"), "release-notes",
                     vault_skills_dir(a_machine.vault) / "release-notes") is None


def test_a_folder_already_in_the_vault_is_a_collision(a_machine):
    taken = vault_skills_dir(a_machine.vault) / "release-notes"
    taken.mkdir()

    message = collision(a_machine.manifest.read_text(encoding="utf-8"), "release-notes", taken)

    assert message and "already in the Vault" in message


def test_a_name_already_declared_is_a_collision_even_when_quoted(a_machine):
    text = 'schema_version: 1\nskills:\n  "release-notes":\n    origin: vault\n'

    message = collision(text, "release-notes", a_machine.home / "nowhere")

    assert message and "already declared" in message


def test_the_new_entry_appends_and_leaves_the_old_one_alone():
    before = "schema_version: 1\nskills:\n  kept:\n    origin: vault\n"

    after = append_entry(before, "release-notes", 'writes "notes"', ["claude", "openclaw"])

    assert after.startswith(before.rstrip("\n"))
    assert after.endswith("\n")
    assert "  release-notes:\n    origin: vault\n    exposure: manual\n" in after
    assert "    targets: [claude, openclaw]\n" in after
    assert '    description: "writes \\"notes\\""\n' in after


def test_an_entry_without_a_description_is_still_a_valid_entry():
    after = append_entry("skills:\n", "quiet", "", ["claude"])

    assert "description:" not in after
    assert "  quiet:\n" in after


# -------------------------------------------------------------------- adoption


def test_an_adopted_skill_lands_in_the_vault_the_manifest_and_the_views(a_machine, tmp_path):
    skill = a_learned_skill(tmp_path)
    recorder = Recorder()

    outcome = adopt_skill(
        skill, "writes the release notes",
        home=a_machine.home, vault_data=a_machine.vault, engine_root=a_machine.engine,
        state_dir=a_machine.state, run=recorder, which=on_path(),
    )

    assert outcome.adopted, outcome.note
    copied = vault_skills_dir(a_machine.vault) / "release-notes" / "SKILL.md"
    assert copied.is_file()
    manifest = a_machine.manifest.read_text(encoding="utf-8")
    assert "  release-notes:\n    origin: vault\n    exposure: manual\n" in manifest
    assert f"    targets: [{', '.join(ADOPTED_TARGETS)}]\n" in manifest
    assert '    description: "writes the release notes"\n' in manifest
    # The view every runtime reads, and OpenClaw reads this one.
    assert (a_machine.home / ".agents" / "skills" / "release-notes" / "SKILL.md").is_file()
    assert recorder.calls == [[
        "openclaw", "skills", "workshop", "archive", "release-notes",
        "--reason", ARCHIVE_REASON, "--agent", "main",
    ]]


def test_the_workshop_copy_is_archived_after_the_canonical_one_exists(a_machine, tmp_path):
    """The order matters: a failed archive still leaves the canonical skill in every CLI."""
    skill = a_learned_skill(tmp_path)

    def run(argv: list[str]) -> RunResult:
        assert (vault_skills_dir(a_machine.vault) / "release-notes" / "SKILL.md").is_file()
        return RunResult(returncode=0, stdout="", stderr="")

    outcome = adopt_skill(
        skill, "notes", home=a_machine.home, vault_data=a_machine.vault,
        engine_root=a_machine.engine, state_dir=a_machine.state, run=run, which=on_path(),
    )

    assert outcome.adopted


def test_a_refused_archive_does_not_undo_the_adoption(a_machine, tmp_path):
    skill = a_learned_skill(tmp_path)

    outcome = adopt_skill(
        skill, "notes", home=a_machine.home, vault_data=a_machine.vault,
        engine_root=a_machine.engine, state_dir=a_machine.state,
        run=Recorder(returncode=1, stderr="no such skill"), which=on_path(),
    )

    assert outcome.adopted
    assert "OpenClaw refused the archive: no such skill" in outcome.note
    assert (a_machine.home / ".agents" / "skills" / "release-notes" / "SKILL.md").is_file()


def test_without_openclaw_on_path_the_adoption_still_happens_and_says_so(a_machine, tmp_path):
    skill = a_learned_skill(tmp_path)
    recorder = Recorder()

    outcome = adopt_skill(
        skill, "notes", home=a_machine.home, vault_data=a_machine.vault,
        engine_root=a_machine.engine, state_dir=a_machine.state,
        run=recorder, which=lambda name: None,
    )

    assert outcome.adopted
    assert "not on PATH" in outcome.note
    assert recorder.calls == []


def test_a_taken_name_is_refused_and_nothing_is_overwritten(a_machine, tmp_path):
    skill = a_learned_skill(tmp_path)
    taken = vault_skills_dir(a_machine.vault) / "release-notes"
    taken.mkdir()
    (taken / "SKILL.md").write_text("someone else's work\n", encoding="utf-8")
    recorder = Recorder()

    outcome = adopt_skill(
        skill, "notes", home=a_machine.home, vault_data=a_machine.vault,
        engine_root=a_machine.engine, state_dir=a_machine.state, run=recorder, which=on_path(),
    )

    assert not outcome.adopted
    assert "nothing was overwritten" in outcome.note
    assert (taken / "SKILL.md").read_text(encoding="utf-8") == "someone else's work\n"
    assert recorder.calls == []


def test_a_declared_name_is_refused_before_anything_is_copied(a_machine, tmp_path):
    declared = a_machine.manifest.read_text(encoding="utf-8").replace(
        "skills:\n",
        "skills:\n  declared:\n    origin: vault\n    exposure: manual\n    targets: [claude]\n",
        1,
    )
    a_machine.manifest.write_text(declared, encoding="utf-8")
    skill = a_learned_skill(tmp_path, name="declared")
    recorder = Recorder()

    outcome = adopt_skill(
        skill, "notes", home=a_machine.home, vault_data=a_machine.vault,
        engine_root=a_machine.engine, state_dir=a_machine.state, run=recorder, which=on_path(),
    )

    assert not outcome.adopted
    assert "already declared" in outcome.note
    assert not (vault_skills_dir(a_machine.vault) / "declared").exists()
    assert recorder.calls == []


def test_a_machine_whose_vault_has_no_manifest_is_not_this_library(a_machine, tmp_path):
    a_machine.manifest.unlink()
    skill = a_learned_skill(tmp_path)

    outcome = adopt_skill(
        skill, "notes", home=a_machine.home, vault_data=a_machine.vault,
        engine_root=a_machine.engine, state_dir=a_machine.state, run=Recorder(), which=on_path(),
    )

    assert not outcome.adopted
    assert "no skills manifest" in outcome.note
