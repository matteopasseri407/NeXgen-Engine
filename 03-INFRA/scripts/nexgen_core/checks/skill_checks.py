"""Consistency checks for the skill catalog and its views."""
from __future__ import annotations

from pathlib import Path

from nexgen_core.config import load_skills_manifest
from nexgen_core.paths import skills_manifest
from nexgen_core.report import CheckOutcome, Severity
from nexgen_core.skills import SkillMaterializer, is_safe_skill_name


def check_skills_manifest(manifest_path: Path) -> CheckOutcome:
    """Check that skills.manifest.yaml is present and valid."""
    if not manifest_path.is_file():
        return CheckOutcome(
            id="skills.manifest_present",
            severity=Severity.BROKEN,
            message=f"The skills manifest '{manifest_path}' does not exist.",
            action="Create or restore skills.manifest.yaml from the templates.",
        )

    try:
        data = load_skills_manifest(manifest_path)
        skill_count = len(data.get("skills", {}))
        return CheckOutcome(
            id="skills.manifest_valid",
            severity=Severity.OK,
            message=f"Skills manifest valid with {skill_count} skills declared",
        )
    except Exception as exc:
        return CheckOutcome(
            id="skills.manifest_valid",
            severity=Severity.BROKEN,
            message=f"The skills manifest contains errors: {exc}",
            action="Fix the syntax of skills.manifest.yaml.",
        )


def check_skill_library_and_index(vault_data: Path, home: Path) -> CheckOutcome:
    """Check that the skill library and the INDEX.md index exist."""
    index_file = home / ".agents" / "skills" / "INDEX.md"
    mat = SkillMaterializer(vault_data=vault_data, home=home)

    def remedy() -> bool:
        mat.materialize(apply=True)
        return True

    if not index_file.is_file():
        return CheckOutcome(
            id="skills.index_present",
            severity=Severity.BROKEN,
            message="The skill catalog (~/.agents/skills/INDEX.md) is not present.",
            action="Run 'agent-sync apply' to materialize the skills and regenerate the index.",
            remedy=remedy,
        )

    return CheckOutcome(
        id="skills.index_present",
        severity=Severity.OK,
        message="Skill catalog present and aligned",
    )


#: Map of target -> SkillMaterializer attributes that hold the active view
#: for that runtime. Reuses exactly the same attributes that
#: SkillMaterializer.materialize() populates, so this map can't diverge
#: from where the views are actually written.
_TARGET_VIEW_ATTRS: dict[str, tuple[str, ...]] = {
    "claude": ("claude_dir",),
    "antigravity": ("gemini_dir", "gemini_config_dir", "gemini_legacy_dir"),
    "codex": ("codex_dir",),
    "opencode": ("active_dir", "opencode_dir"),
}


def _library_skill_entries(library_dir: Path) -> list[Path]:
    """Only the library entries that are actually skills: a safe name and
    (a symlink or a folder with SKILL.md). Excludes quarantine/legacy and
    accessory files like INDEX.md."""
    if not library_dir.is_dir():
        return []
    entries = []
    for p in sorted(library_dir.iterdir()):
        if not is_safe_skill_name(p.name):
            continue
        if p.is_symlink() or (p / "SKILL.md").is_file():
            entries.append(p)
    return entries


def check_skill_library_symlinks(home: Path) -> CheckOutcome:
    """Skill library entries must not be broken or self-referential
    symlinks. They are never removed automatically (the sync is additive by
    contract), so they must be reported for manual cleanup."""
    library_dir = home / ".agents" / "skill-library"
    entries = _library_skill_entries(library_dir)

    broken = [p.name for p in entries if p.is_symlink() and not p.exists()]
    if broken:
        return CheckOutcome(
            id="skills.library_symlinks",
            severity=Severity.BROKEN,
            message=f"Skill library entry/entries with a broken or self-referential symlink: {', '.join(broken)}.",
            action="Run 'agent-sync apply' (skills-sync) to regenerate the library; remove the entries manually if they stay broken.",
        )
    return CheckOutcome(
        id="skills.library_symlinks",
        severity=Severity.OK,
        message="No broken symlinks in the skill library",
    )


def check_skills_out_of_manifest(vault_data: Path, home: Path) -> CheckOutcome:
    """Skills materialized in the library but absent from the manifest.

    They are never deleted (the sync is additive by contract): they are an
    undeclared mismatch to be knowingly adopted or abandoned, not something
    this check can decide on its own, which is why it's undetermined and
    not broken.
    """
    mat = SkillMaterializer(vault_data=vault_data, home=home)
    library_dir = home / ".agents" / "skill-library"
    materialized = {p.name for p in _library_skill_entries(library_dir)}
    declared = set(mat.load_manifest().keys())

    stray = sorted(materialized - declared)
    if stray:
        return CheckOutcome(
            id="skills.out_of_manifest",
            severity=Severity.UNDETERMINED,
            message=f"Skills materialized but not declared in the manifest: {', '.join(stray)}.",
            action="Adopt the useful skills by adding them to skills.manifest.yaml, or remove them manually from the library (the sync won't delete them on its own).",
        )
    return CheckOutcome(
        id="skills.out_of_manifest",
        severity=Severity.OK,
        message="No out-of-manifest skills to reconcile",
    )


def check_skills_not_materialized(vault_data: Path, home: Path) -> CheckOutcome:
    """Every skill declared in the manifest must be materialized in the non-discovered library."""
    mat = SkillMaterializer(vault_data=vault_data, home=home)
    library_dir = home / ".agents" / "skill-library"

    def remedy() -> bool:
        mat.materialize(apply=True)
        return True

    declared = mat.load_manifest()
    missing = sorted(name for name in declared if not (library_dir / name / "SKILL.md").is_file())

    if missing:
        return CheckOutcome(
            id="skills.declared_but_not_materialized",
            severity=Severity.BROKEN,
            message=f"Skills declared in the manifest but not materialized: {', '.join(missing)}.",
            action="Run 'agent-sync apply' (skills-sync) to materialize them.",
            remedy=remedy,
        )
    return CheckOutcome(
        id="skills.declared_but_not_materialized",
        severity=Severity.OK,
        message="All skills declared in the manifest are materialized",
    )


def check_engine_starter_views(vault_data: Path, home: Path) -> CheckOutcome:
    """The manifest's starter commands (`origin: engine`, eager/core
    exposure) must exist as an active view in every CLI target they
    declare, not only in the library: they are the commands the user
    invokes directly (e.g. '/nexgen-doctor')."""
    mat = SkillMaterializer(vault_data=vault_data, home=home)

    def remedy() -> bool:
        mat.materialize(apply=True)
        return True

    starters = {
        name: entry
        for name, entry in mat.load_manifest().items()
        if entry.origin == "engine" and entry.exposure in ("eager", "core")
    }
    if not starters:
        return CheckOutcome(
            id="skills.engine_starter_views",
            severity=Severity.OK,
            message="No engine starter commands declared in the manifest",
        )

    missing: list[str] = []
    for name, entry in starters.items():
        for target in entry.targets:
            for attr in _TARGET_VIEW_ATTRS.get(target, ()):
                view_dir = getattr(mat, attr) / name
                if not (view_dir / "SKILL.md").is_file():
                    missing.append(f"{name} ({target})")
                    break

    if missing:
        return CheckOutcome(
            id="skills.engine_starter_views",
            severity=Severity.BROKEN,
            message=f"Engine starter command(s) not materialized as an active view: {', '.join(missing)}.",
            action="Run 'agent-sync apply' (skills-sync) to regenerate the views.",
            remedy=remedy,
        )
    return CheckOutcome(
        id="skills.engine_starter_views",
        severity=Severity.OK,
        message="All engine starter commands are materialized as an active view",
    )


def check_skills_manifest_semantics(vault_data: Path, home: Path) -> CheckOutcome | None:
    """Semantic validation of the manifest (safe names, known origins,
    complete pins, source SKILL.md present): reuses
    SkillMaterializer.validate_manifest() instead of rewriting the same
    rules here."""
    manifest_file = skills_manifest(vault_data)
    if not manifest_file.is_file():
        return None  # already reported by check_skills_manifest

    mat = SkillMaterializer(vault_data=vault_data, home=home)
    problems = mat.validate_manifest()
    if problems:
        return CheckOutcome(
            id="skills.manifest_semantics",
            severity=Severity.BROKEN,
            message=f"{len(problems)} problem(s) in the skills manifest.",
            action="Fix the entries listed: " + "; ".join(problems[:5]),
            detail="; ".join(problems),
        )
    return CheckOutcome(
        id="skills.manifest_semantics",
        severity=Severity.OK,
        message="Skills manifest is semantically valid",
    )
