"""OpenClaw as a skill target: it reads the shared `~/.agents/skills` catalog.

OpenClaw discovers `~/.agents/skills` on its own, which is already the `active_dir`
of the materializer, so the target needs a view in no new folder. What it does
need is the rest of the system knowing it: the view must be written for a skill
that names it, must NOT be written for one that does not, and the pruning pass
must not delete it in the same cycle because the folder is shared with OpenCode.
"""
from __future__ import annotations

from pathlib import Path

from nexgen_core.checks.skill_checks import check_engine_starter_views
from nexgen_core.report import Severity
from nexgen_core.skills import SkillMaterializer

MANIFEST_HEADER = "skills:\n"


def a_machine(tmp_path: Path, entries: str) -> tuple[SkillMaterializer, Path]:
    """A fake vault, home and engine with `entries` under `skills:`."""
    vault = tmp_path / "vault"
    home = tmp_path / "home"
    engine = tmp_path / "engine"
    home.mkdir(parents=True, exist_ok=True)
    (engine / "agent-universal-layer" / "skills").mkdir(parents=True, exist_ok=True)
    skills_dir = vault / "03-INFRA" / "agent-universal-layer" / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    (skills_dir / "skills.manifest.yaml").write_text(MANIFEST_HEADER + entries, encoding="utf-8")
    return SkillMaterializer(vault_data=vault, home=home, engine_root=engine), vault


def a_vault_skill(vault: Path, name: str) -> Path:
    src = vault / "03-INFRA" / "agent-universal-layer" / "skills" / name
    src.mkdir(parents=True, exist_ok=True)
    (src / "SKILL.md").write_text(f"---\ndescription: {name} skill\n---\n# {name}\n", encoding="utf-8")
    return src


def shared_view(mat: SkillMaterializer, name: str) -> Path:
    return mat.active_dir / name / "SKILL.md"


def test_a_skill_that_names_openclaw_lands_in_the_shared_catalog(tmp_path):
    mat, vault = a_machine(tmp_path, "  demo:\n    origin: vault\n    targets: [openclaw]\n")
    a_vault_skill(vault, "demo")

    changes, actions = mat.materialize(apply=True)

    assert changes > 0, actions
    assert shared_view(mat, "demo").is_file()
    assert not (mat.claude_dir / "demo").exists()
    assert not (mat.codex_dir / "demo").exists()


def test_a_skill_that_does_not_name_openclaw_never_reaches_the_shared_catalog(tmp_path):
    mat, vault = a_machine(tmp_path, "  demo:\n    origin: vault\n    targets: [claude]\n")
    a_vault_skill(vault, "demo")

    mat.materialize(apply=True)

    assert (mat.claude_dir / "demo" / "SKILL.md").is_file()
    assert not (mat.active_dir / "demo").exists()


def test_a_skill_that_names_opencode_still_lands_in_the_shared_catalog(tmp_path):
    mat, vault = a_machine(tmp_path, "  demo:\n    origin: vault\n    targets: [opencode]\n")
    a_vault_skill(vault, "demo")

    mat.materialize(apply=True)

    assert shared_view(mat, "demo").is_file()


def test_the_shared_view_survives_the_pruning_pass_of_the_same_cycle(tmp_path):
    """The folder belongs to OpenCode and OpenClaw at once: asking "whose is it" and taking
    the first answer deleted, in the same cycle, the view the other target had just made."""
    mat, vault = a_machine(tmp_path, "  demo:\n    origin: vault\n    targets: [openclaw]\n")
    a_vault_skill(vault, "demo")

    mat.materialize(apply=True)
    first = shared_view(mat, "demo").read_bytes()
    changes, actions = mat.materialize(apply=True)

    assert changes == 0, actions
    assert actions == []
    assert shared_view(mat, "demo").read_bytes() == first


def test_openclaw_is_accepted_as_a_manifest_target(tmp_path):
    mat, vault = a_machine(tmp_path, "  demo:\n    origin: vault\n    targets: [openclaw]\n")
    a_vault_skill(vault, "demo")

    assert mat.validate_manifest() == []


def test_the_catalog_names_the_targets_including_openclaw(tmp_path):
    mat, vault = a_machine(tmp_path, "  demo:\n    origin: vault\n    targets: [openclaw]\n")
    a_vault_skill(vault, "demo")

    mat.materialize(apply=True)

    index = (mat.active_dir / "INDEX.md").read_text(encoding="utf-8")
    assert "| Targets |" in index
    assert "openclaw" in index


def test_the_doctor_finds_a_starter_command_in_the_openclaw_view(tmp_path):
    """`origin: engine` + core exposure must exist in every runtime the entry names,
    and OpenClaw's runtime is the shared catalog."""
    mat, _vault = a_machine(
        tmp_path,
        "  starter:\n    origin: engine\n    exposure: core\n    targets: [openclaw]\n",
    )
    source = mat.engine_root / "agent-universal-layer" / "skills" / "starter"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("---\ndescription: starter\n---\n# starter\n", encoding="utf-8")

    mat.materialize(apply=True)
    outcome = check_engine_starter_views(
        mat.vault_data, mat.home,
    )

    assert outcome.severity is Severity.OK, outcome.message


def test_a_missing_openclaw_view_is_reported_as_a_broken_starter(tmp_path):
    mat, _vault = a_machine(
        tmp_path,
        "  starter:\n    origin: engine\n    exposure: core\n    targets: [openclaw]\n",
    )
    source = mat.engine_root / "agent-universal-layer" / "skills" / "starter"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("---\ndescription: starter\n---\n# starter\n", encoding="utf-8")

    outcome = check_engine_starter_views(mat.vault_data, mat.home)

    assert outcome.severity is Severity.BROKEN
    assert "starter (openclaw)" in outcome.message
