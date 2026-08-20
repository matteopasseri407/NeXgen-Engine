"""Controlli di coerenza del catalogo e delle viste delle skill."""
from __future__ import annotations

from pathlib import Path

from nexgen_core.config import load_skills_manifest
from nexgen_core.paths import skills_manifest
from nexgen_core.report import CheckOutcome, Severity
from nexgen_core.skills import SkillMaterializer, is_safe_skill_name


def check_skills_manifest(manifest_path: Path) -> CheckOutcome:
    """Verifica che skills.manifest.yaml sia presente e valido."""
    if not manifest_path.is_file():
        return CheckOutcome(
            id="skills.manifest_present",
            severity=Severity.BROKEN,
            message=f"Il manifest delle skill '{manifest_path}' non esiste.",
            action="Crea o ripristina skills.manifest.yaml dai template.",
        )

    try:
        data = load_skills_manifest(manifest_path)
        skill_count = len(data.get("skills", {}))
        return CheckOutcome(
            id="skills.manifest_valid",
            severity=Severity.OK,
            message=f"Manifest skill valido con {skill_count} skill dichiarate",
        )
    except Exception as exc:
        return CheckOutcome(
            id="skills.manifest_valid",
            severity=Severity.BROKEN,
            message=f"Il manifest delle skill contiene errori: {exc}",
            action="Correggi la sintassi di skills.manifest.yaml.",
        )


def check_skill_library_and_index(vault_data: Path, home: Path) -> CheckOutcome:
    """Verifica che la libreria di skill e l'indice INDEX.md esistano."""
    index_file = home / ".agents" / "skills" / "INDEX.md"
    mat = SkillMaterializer(vault_data=vault_data, home=home)

    def remedy() -> bool:
        mat.materialize(apply=True)
        return True

    if not index_file.is_file():
        return CheckOutcome(
            id="skills.index_present",
            severity=Severity.BROKEN,
            message="Il catalogo delle skill (~/.agents/skills/INDEX.md) non è presente.",
            action="Esegui 'agent-sync apply' per materializzare le skill e rigenerare l'indice.",
            remedy=remedy,
        )

    return CheckOutcome(
        id="skills.index_present",
        severity=Severity.OK,
        message="Catalogo delle skill presente e allineato",
    )


#: Mappa target -> attributi di SkillMaterializer che ospitano la vista
#: attiva per quel runtime. Riusa esattamente gli stessi attributi che
#: SkillMaterializer.materialize() popola, così questa mappa non può
#: divergere da dove le viste vengono davvero scritte.
_TARGET_VIEW_ATTRS: dict[str, tuple[str, ...]] = {
    "claude": ("claude_dir",),
    "antigravity": ("gemini_dir", "gemini_config_dir", "gemini_legacy_dir"),
    "codex": ("codex_dir",),
    "opencode": ("active_dir", "opencode_dir"),
}


def _library_skill_entries(library_dir: Path) -> list[Path]:
    """Le sole voci della libreria che sono davvero skill: nome sicuro e
    (symlink oppure cartella con SKILL.md). Esclude la quarantena/legacy e
    file accessori come INDEX.md."""
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
    """Le voci della libreria skill non devono essere symlink rotti o
    auto-referenziali. Non vengono mai rimosse in automatico (il sync è
    additivo per contratto), quindi vanno segnalate per una pulizia manuale."""
    library_dir = home / ".agents" / "skill-library"
    entries = _library_skill_entries(library_dir)

    broken = [p.name for p in entries if p.is_symlink() and not p.exists()]
    if broken:
        return CheckOutcome(
            id="skills.library_symlinks",
            severity=Severity.BROKEN,
            message=f"Voce/i della libreria skill con symlink rotto o auto-referenziale: {', '.join(broken)}.",
            action="Esegui 'agent-sync apply' (skills-sync) per rigenerare la libreria; rimuovi manualmente le voci se restano rotte.",
        )
    return CheckOutcome(
        id="skills.library_symlinks",
        severity=Severity.OK,
        message="Nessun symlink rotto nella libreria skill",
    )


def check_skills_out_of_manifest(vault_data: Path, home: Path) -> CheckOutcome:
    """Skill materializzate nella libreria ma assenti dal manifest.

    Non vengono mai cancellate (il sync è additivo per contratto): sono un
    disallineamento non dichiarato da adottare o abbandonare
    consapevolmente, non qualcosa che questo controllo può stabilire da
    solo, per questo è undetermined e non broken.
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
            message=f"Skill materializzate ma non dichiarate nel manifest: {', '.join(stray)}.",
            action="Adotta le skill utili aggiungendole a skills.manifest.yaml, oppure rimuovile manualmente dalla libreria (il sync non le cancella da solo).",
        )
    return CheckOutcome(
        id="skills.out_of_manifest",
        severity=Severity.OK,
        message="Nessuna skill fuori manifest da riconciliare",
    )


def check_skills_not_materialized(vault_data: Path, home: Path) -> CheckOutcome:
    """Ogni skill dichiarata nel manifest deve essere materializzata nella libreria non-discovered."""
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
            message=f"Skill dichiarate nel manifest ma non materializzate: {', '.join(missing)}.",
            action="Esegui 'agent-sync apply' (skills-sync) per materializzarle.",
            remedy=remedy,
        )
    return CheckOutcome(
        id="skills.declared_but_not_materialized",
        severity=Severity.OK,
        message="Tutte le skill dichiarate nel manifest sono materializzate",
    )


def check_engine_starter_views(vault_data: Path, home: Path) -> CheckOutcome:
    """I comandi starter (`origin: engine`, esposizione eager/core) del
    manifest devono esistere come vista attiva in ogni CLI target che
    dichiarano, non solo nella libreria: sono i comandi che l'utente invoca
    direttamente (es. '/nexgen-doctor')."""
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
            message="Nessun comando starter del motore dichiarato nel manifest",
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
            message=f"Comando/i starter del motore non materializzati come vista attiva: {', '.join(missing)}.",
            action="Esegui 'agent-sync apply' (skills-sync) per rigenerare le viste.",
            remedy=remedy,
        )
    return CheckOutcome(
        id="skills.engine_starter_views",
        severity=Severity.OK,
        message="Tutti i comandi starter del motore sono materializzati come vista attiva",
    )


def check_skills_manifest_semantics(vault_data: Path, home: Path) -> CheckOutcome | None:
    """Validazione semantica del manifest (nomi sicuri, origini note, pin
    completi, sorgente SKILL.md presente): riusa
    SkillMaterializer.validate_manifest() invece di riscrivere le stesse
    regole qui."""
    manifest_file = skills_manifest(vault_data)
    if not manifest_file.is_file():
        return None  # già segnalato da check_skills_manifest

    mat = SkillMaterializer(vault_data=vault_data, home=home)
    problems = mat.validate_manifest()
    if problems:
        return CheckOutcome(
            id="skills.manifest_semantics",
            severity=Severity.BROKEN,
            message=f"{len(problems)} problema/i nel manifest delle skill.",
            action="Correggi le voci indicate: " + "; ".join(problems[:5]),
            detail="; ".join(problems),
        )
    return CheckOutcome(
        id="skills.manifest_semantics",
        severity=Severity.OK,
        message="Manifest delle skill semanticamente valido",
    )
