#!/usr/bin/env python3
"""Materializzazione e sincronizzazione delle skill per NeXgen Engine v2.

Gestisce le 4 origini delle skill:
1. vault: posseduta dall'utente, portata da Git nei dati privati.
2. engine: posseduta dal prodotto, letta dal motore installato senza duplicazioni.
3. github: terze parti, pinnata a un commit immutabile e clonata/scaricata.
4. installer: terze parti con installatore dedicato, pinnata a una versione.

Mantiene la libreria non-discovered (~/.agents/skill-library/) e le viste native per le 4 CLI.
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.config import (
    SKILL_EXPOSURES,
    SKILL_ORIGINS,
    SKILL_TARGETS,
    load_skills_manifest,
)
from nexgen_core.paths import resolve_engine_root, resolve_vault_data, skills_manifest

IS_WINDOWS = platform.system() == "Windows"


@dataclass
class SkillEntry:
    name: str
    origin: str = "vault"
    exposure: str = "lazy"
    scope: str = "shared"
    owner: str | None = None
    targets: list[str] = field(default_factory=lambda: ["claude", "codex", "antigravity", "opencode"])
    repo: str | None = None
    commit: str | None = None
    version: str | None = None
    path: str | None = None
    description: str = ""
    source_path: Path | None = None


#: Un nome di skill è un segmento di percorso, non un percorso: niente
#: separatori, niente risalite. Senza questo controllo `agent-skill show
#: ../../qualcosa` legge un file fuori dalla libreria.
SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

#: Un pin che non è un commit completo non è un pin: un branch o un tag si
#: sposta sotto i piedi e la skill cambia senza che nessuno l'abbia scelto.
COMMIT_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")

#: Un clone che non risponde non deve tenere fermo il ciclo di guardia.
GIT_CLONE_TIMEOUT_SECONDS = 120

#: Git non deve mai fermarsi a chiedere credenziali dentro un timer.
GIT_NONINTERACTIVE_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GCM_INTERACTIVE": "Never",
}


def is_safe_skill_name(name: str) -> bool:
    """Vero se il nome può diventare un segmento di percorso senza sorprese."""
    return bool(name) and name not in (".", "..") and bool(SKILL_NAME_RE.match(name))


def same_tree_content(src: Path, dst: Path) -> bool:
    """Vero se due alberi contengono esattamente gli stessi file, byte per byte."""
    if not src.is_dir() or not dst.is_dir():
        return False
    left = {p.relative_to(src): p for p in src.rglob("*") if p.is_file()}
    right = {p.relative_to(dst): p for p in dst.rglob("*") if p.is_file()}
    if left.keys() != right.keys():
        return False
    try:
        return all(left[k].read_bytes() == right[k].read_bytes() for k in left)
    except OSError:
        return False


def next_backup_path(path: Path) -> Path:
    """Un percorso di backup libero accanto a `path`."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    candidate = path.with_name(f"{path.name}.bak-{stamp}")
    n = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.bak-{stamp}-{n}")
        n += 1
    return candidate


def make_link_or_copy(src: Path, dst: Path) -> bool:
    """Crea un symlink (o copia su Windows se i privilegi symlink non sono attivi).

    Una cartella reale trovata al posto della vista non viene mai cancellata:
    può contenere lavoro che nessuno ci ha affidato. Se il contenuto è già
    identico non si tocca niente, altrimenti si mette da parte con un backup
    prima di prendere il posto.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.is_symlink() or dst.is_file():
        try:
            if dst.is_symlink() and dst.resolve() == src.resolve():
                return False
            dst.unlink()
        except OSError:
            pass
    elif dst.is_dir():
        if same_tree_content(src, dst):
            return False
        dst.rename(next_backup_path(dst))

    try:
        dst.symlink_to(src, target_is_directory=src.is_dir())
        return True
    except OSError:
        # Fallback copia per Windows senza symlink developer mode
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)
        return True


class SkillMaterializer:
    """Materializza la libreria di skill e genera le viste native per le CLI."""

    def __init__(
        self,
        vault_data: Path | None = None,
        engine_root: Path | None = None,
        home: Path | None = None,
    ) -> None:
        self.home = home or Path.home()
        _v = resolve_vault_data(self.home, vault_data)
        self.vault_data = _v
        self.engine_root = resolve_engine_root(self.home, engine_root)

        self.library_dir = self.home / ".agents" / "skill-library"
        self.active_dir = self.home / ".agents" / "skills"
        self.claude_dir = self.home / ".claude" / "skills"
        self.gemini_dir = self.home / ".gemini" / "antigravity-cli" / "skills"
        self.gemini_config_dir = self.home / ".gemini" / "config" / "skills"
        self.gemini_legacy_dir = self.home / ".gemini" / "skills"
        self.codex_dir = self.home / ".codex" / "skills"
        self.opencode_dir = self.home / ".opencode" / "skills"

    def load_manifest(self) -> dict[str, SkillEntry]:
        manifest_file = skills_manifest(self.vault_data)
        if not manifest_file.is_file():
            return {}

        data = load_skills_manifest(manifest_file)
        skills: dict[str, SkillEntry] = {}
        for name, raw in data.get("skills", {}).items():
            entry = SkillEntry(
                name=name,
                origin=raw.get("origin", "vault"),
                exposure=raw.get("exposure", "lazy"),
                scope=raw.get("scope", "shared"),
                owner=raw.get("owner"),
                targets=raw.get("targets", ["claude", "codex", "antigravity", "opencode"]),
                repo=raw.get("repo"),
                commit=raw.get("commit"),
                version=raw.get("version"),
                path=raw.get("path") or raw.get("sub"),
                description=raw.get("description", ""),
            )
            # Risoluzione percorso sorgente
            if entry.origin == "vault":
                entry.source_path = self.vault_data / "03-INFRA" / "agent-universal-layer" / "skills" / name
            elif entry.origin == "engine":
                entry.source_path = self.engine_root / "agent-universal-layer" / "skills" / name
            skills[name] = entry

        return skills

    def _belongs_here(self, entry: SkillEntry) -> bool:
        """Questa skill riguarda chi sta usando questa macchina?

        `scope` e `owner` venivano letti dal manifest e non applicati a
        niente: un campo che si può scrivere e che non ha effetto è peggio di
        un campo che manca, perché fa credere di aver deciso qualcosa.

        Su una installazione a operatore singolo — nessun `AGENT_TEAM_MEMBER`
        dichiarato — tutto appartiene a chi c'è, e niente viene saltato.
        """
        if entry.scope != "personal":
            return True
        member = os.environ.get("AGENT_TEAM_MEMBER", "").strip()
        if not member:
            return True
        return entry.owner is None or entry.owner == member

    def validate_manifest(self) -> list[str]:
        """Controlla il manifest senza scrivere niente, e dice cosa non torna.

        Serve a scoprire un errore prima che il ciclo di guardia lo incontri:
        una skill dichiarata senza sorgente, un pin che non è un commit, un
        nome che non può diventare una cartella.
        """
        problems: list[str] = []
        manifest_file = skills_manifest(self.vault_data)
        if not manifest_file.is_file():
            return [f"Il manifest delle skill non esiste: {manifest_file}"]

        for name, entry in self.load_manifest().items():
            if not is_safe_skill_name(name):
                problems.append(f"'{name}': il nome non può diventare una cartella")
            if entry.origin not in SKILL_ORIGINS:
                problems.append(f"'{name}': origine '{entry.origin}' sconosciuta")
            if entry.exposure not in SKILL_EXPOSURES:
                problems.append(f"'{name}': esposizione '{entry.exposure}' sconosciuta")
            unknown = [t for t in entry.targets if t not in SKILL_TARGETS]
            if unknown:
                problems.append(f"'{name}': runtime sconosciuti {', '.join(unknown)}")
            if entry.origin == "github":
                if not entry.repo:
                    problems.append(f"'{name}': origine github senza 'repo'")
                if not entry.commit:
                    problems.append(f"'{name}': origine github senza 'commit'")
                elif not COMMIT_SHA_RE.match(entry.commit):
                    problems.append(
                        f"'{name}': il pin '{entry.commit}' non è un commit completo a 40 caratteri"
                    )
            if entry.origin == "installer" and not entry.version:
                problems.append(f"'{name}': origine installer senza 'version'")
            if entry.origin in ("vault", "engine"):
                src = entry.source_path
                if src is None or not (src / "SKILL.md").is_file():
                    problems.append(f"'{name}': manca il file SKILL.md sotto {src}")
        return problems

    def _ensure_github_checkout(self, cache_dir: Path, entry: SkillEntry) -> tuple[bool, str | None]:
        """Porta la cache locale esattamente sul commit dichiarato.

        Una cache già presente non basta: se il manifest alza il pin, la copia
        vecchia va aggiornata. Prima si controlla dove sta davvero la cache,
        e solo se diverge si va a prendere il commit nuovo.
        """
        env = {**os.environ, **GIT_NONINTERACTIVE_ENV}

        def git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
            base = ["git"] + (["-C", str(cwd)] if cwd else [])
            return subprocess.run(
                base + list(args),
                capture_output=True, text=True, check=False,
                timeout=GIT_CLONE_TIMEOUT_SECONDS, env=env,
            )

        try:
            if cache_dir.is_dir():
                head = git("rev-parse", "HEAD", cwd=cache_dir)
                if head.returncode == 0 and head.stdout.strip().lower() == entry.commit.lower():
                    return True, None
                fetched = git("fetch", "--quiet", "origin", entry.commit, cwd=cache_dir)
                if fetched.returncode != 0:
                    git("fetch", "--quiet", "--all", cwd=cache_dir)
            else:
                cache_dir.parent.mkdir(parents=True, exist_ok=True)
                res = git("clone", "--quiet", entry.repo or "", str(cache_dir))
                if res.returncode != 0:
                    return False, (
                        f"[ERRORE] Skill github '{entry.name}': clonazione di {entry.repo} "
                        f"fallita: {res.stderr.strip()}"
                    )

            res = git("checkout", "--quiet", "--detach", entry.commit, cwd=cache_dir)
            if res.returncode != 0:
                return False, (
                    f"[ERRORE] Skill github '{entry.name}': il commit {entry.commit} "
                    f"non è raggiungibile nel repository: {res.stderr.strip()}"
                )
            return True, None
        except subprocess.TimeoutExpired:
            return False, (
                f"[ERRORE] Skill github '{entry.name}': {entry.repo} non ha risposto entro "
                f"{GIT_CLONE_TIMEOUT_SECONDS}s, riprovo al giro successivo"
            )
        except OSError as exc:
            return False, f"[ERRORE] Skill github '{entry.name}': {exc}"

    def materialize(self, apply: bool = True) -> tuple[int, list[str]]:
        """Materializza tutte le skill nella libreria e crea le viste native."""
        skills = self.load_manifest()
        actions: list[str] = []
        changes = 0

        self.library_dir.mkdir(parents=True, exist_ok=True)
        self.active_dir.mkdir(parents=True, exist_ok=True)

        for name, entry in skills.items():
            if not self._belongs_here(entry):
                continue
            lib_dest = self.library_dir / name

            # Se la sorgente locale esiste, colleghiamo alla libreria
            if entry.source_path and entry.source_path.is_dir():
                if apply:
                    if make_link_or_copy(entry.source_path, lib_dest):
                        changes += 1
                        actions.append(f"Collegata skill '{name}' alla libreria")
            elif entry.origin == "github" and entry.repo and entry.commit:
                cache_dir = self.home / ".agents" / "cache" / "github-skills" / name
                if not COMMIT_SHA_RE.match(entry.commit):
                    actions.append(
                        f"[ERRORE] Skill github '{name}': il pin '{entry.commit}' non è un commit "
                        f"completo a 40 caratteri, salto la voce"
                    )
                    continue

                clone_success = False
                if apply:
                    clone_success, problem = self._ensure_github_checkout(cache_dir, entry)
                    if problem:
                        actions.append(problem)

                if clone_success and apply:
                    # Il manifest può puntare a una sottocartella del repo. Il
                    # confine va verificato: un `path` con una risalita
                    # collegherebbe qualcosa che sta fuori dal clone.
                    source = cache_dir
                    if entry.path:
                        candidate = (cache_dir / entry.path).resolve()
                        if not candidate.is_relative_to(cache_dir.resolve()):
                            actions.append(
                                f"[ERRORE] Skill github '{name}': il percorso '{entry.path}' "
                                f"esce dal repository clonato, salto la voce"
                            )
                            continue
                        source = candidate
                    if make_link_or_copy(source, lib_dest):
                        changes += 1
                        actions.append(f"Collegata skill github '{name}' alla libreria")

            # Generazione viste attive (se exposure == eager o core)
            if entry.exposure in ("eager", "core") and lib_dest.is_dir() and apply:
                for target in entry.targets:
                    target_dirs: list[Path] = []
                    if target == "claude":
                        target_dirs = [self.claude_dir]
                    elif target == "antigravity":
                        target_dirs = [self.gemini_dir, self.gemini_config_dir, self.gemini_legacy_dir]
                    elif target == "codex":
                        target_dirs = [self.codex_dir]
                    elif target == "opencode":
                        target_dirs = [self.active_dir, self.opencode_dir]

                    for tdir in target_dirs:
                        dest = tdir / name
                        if make_link_or_copy(lib_dest, dest):
                            changes += 1
                            actions.append(f"Creata vista attiva '{name}' per {target}")

        # Rigenera INDEX.md
        if apply:
            self.generate_index(skills)

        return changes, actions

    def generate_index(self, skills: dict[str, SkillEntry] | None = None) -> Path:
        """Genera il file ~/.agents/skills/INDEX.md con l'indice di tutte le skill."""
        if skills is None:
            skills = self.load_manifest()

        self.active_dir.mkdir(parents=True, exist_ok=True)
        index_file = self.active_dir / "INDEX.md"

        lines = [
            "# Catalogo Skill NeXgen Engine",
            "",
            "Tutte le skill disponibili nel sistema (caricate on-demand tramite `agent-skill find` o `agent-skill show`).",
            "",
            "| Skill | Origine | Esposizione | Descrizione |",
            "|---|---|---|---|",
        ]

        for name in sorted(skills.keys()):
            s = skills[name]
            desc = s.description or "-"
            lines.append(f"| `{name}` | `{s.origin}` | `{s.exposure}` | {desc} |")

        lines.append("")
        index_file.write_text("\n".join(lines), encoding="utf-8")
        return index_file

    def migrate_legacy(self, apply: bool = True) -> list[str]:
        """Quarantena delle viste eager legacy fuori dalle root di discovery.

        Port da skills-sync.py --migrate-legacy della release: le installazioni
        vecchie mettevano skill terze direttamente sotto le root discovery.
        Non vanno cancellate né spostate silenziosamente dal guard ricorrente;
        --migrate-legacy le preserva in una quarantena locale non indicizzata.
        """
        skills = self.load_manifest()
        actions: list[str] = []
        views = {
            "shared": self.active_dir,
            "codex": self.codex_dir,
            "claude": self.claude_dir,
        }
        legacy_root = self.library_dir / "legacy"
        for scope, root in views.items():
            if not root.is_dir() or root.is_symlink():
                continue
            for entry in sorted(root.iterdir()):
                if entry.name.startswith(".") or entry.name == "INDEX.md":
                    continue
                body = entry / "SKILL.md" if entry.is_dir() else entry
                if not body.is_file():
                    continue
                managed = self.library_dir / entry.name
                spec = skills.get(entry.name)
                expected = (
                    (scope == "shared" and spec is not None and spec.exposure in ("core", "eager"))
                    or (scope in ("claude", "codex") and spec is not None and "claude" in spec.targets)
                )
                if expected and (managed.exists() or managed.is_symlink()):
                    actions.append(f"legacy/{scope}/{entry.name}: vista gestita mantenuta")
                    continue
                destination = legacy_root / scope / entry.name
                if destination.exists() or destination.is_symlink():
                    actions.append(f"legacy/{scope}/{entry.name}: destinazione già esistente, vista lasciata intatta")
                    continue
                if not apply:
                    actions.append(f"legacy/{scope}/{entry.name}: sarebbe messa in quarantena fuori dalle root discovery")
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(entry), str(destination))
                actions.append(f"legacy/{scope}/{entry.name}: messa in quarantena fuori dalle root discovery")
        return actions


def main(argv: list[str] | None = None) -> int:
    """CLI per la gestione e sincronizzazione delle skill."""
    if argv is None:
        argv = sys.argv[1:]

    mat = SkillMaterializer()

    if not argv or argv[0] in ("-h", "--help"):
        print(
            "Uso: agent-skill [list|find|show|path] <argomenti>\n"
            "     skills-sync [apply|index|validate] [--migrate-legacy]"
        )
        return 0

    # Normalizza i flag stile release (--apply, --index, --migrate-legacy)
    # in qualunque posizione, accettando anche la forma posizionale v2.
    argv_l = [a.lower() for a in argv]
    flag_apply = any(a in ("--apply", "--sync") for a in argv_l)
    flag_index = "--index" in argv_l
    flag_migrate = "--migrate-legacy" in argv_l
    positional = [a for a in argv if not a.startswith("-")]

    cmd = positional[0].lower() if positional else ""

    if cmd in ("apply", "sync") or flag_apply:
        changes, actions = mat.materialize(apply=True)
        failed = [a for a in actions if a.startswith("[ERRORE]")]
        for act in actions:
            print(f"  {'✗' if act.startswith('[ERRORE]') else '✓'} {act}")
        if flag_migrate:
            for act in mat.migrate_legacy(apply=True):
                print(f"  ✓ {act}")
        if failed:
            # Un errore stampato e poi un'uscita a zero è il modo in cui una
            # macchina resta indietro senza che nessuno se ne accorga.
            print(
                f"{len(failed)} skill su {changes + len(failed)} non sono state sincronizzate.",
                file=sys.stderr,
            )
            return 1
        print(f"Skill sincronizzate con successo ({changes} modifiche applicate).")
        return 0

    elif cmd == "validate":
        problems = mat.validate_manifest()
        for problem in problems:
            print(f"  ✗ {problem}", file=sys.stderr)
        if problems:
            print(f"Manifest delle skill: {len(problems)} problemi.", file=sys.stderr)
            return 1
        print("Manifest delle skill: nessun problema.")
        return 0

    elif cmd == "index" or flag_index:
        idx = mat.generate_index()
        print(f"Indice generato in {idx}")
        return 0

    elif cmd == "list":
        for name, s in sorted(mat.load_manifest().items()):
            print(f"{name}\t{s.description or '-'}")
        return 0

    elif cmd == "find":
        terms = [t.lower() for t in positional[1:] if t.strip()]
        if not terms:
            print("Uso: agent-skill find <termine> [termine...]", file=sys.stderr)
            return 2
        found = False
        for name, s in sorted(mat.load_manifest().items()):
            haystack = f"{name} {s.description or ''}".lower()
            if all(t in haystack for t in terms):
                print(f"{name}\t{s.description or '-'}")
                found = True
        if not found:
            print(f"Nessuna skill gestita corrisponde a: {' '.join(terms)}", file=sys.stderr)
            return 1
        return 0

    elif cmd in ("show", "path"):
        if len(positional) < 2:
            print(f"Uso: agent-skill {cmd} <nome-skill>", file=sys.stderr)
            return 2
        name = positional[1]
        if not is_safe_skill_name(name):
            print(
                f"'{name}' non è un nome di skill valido: sono ammessi lettere, cifre, "
                f"punto, trattino e trattino basso.",
                file=sys.stderr,
            )
            return 2
        body_file = mat.library_dir / name / "SKILL.md"
        if not body_file.is_file():
            body_file = mat.library_dir / f"{name}.md"
        if body_file.is_file():
            print(body_file.read_text(encoding="utf-8") if cmd == "show" else body_file)
            return 0
        print(_missing_skill_hint(mat, name), file=sys.stderr)
        return 1

    print(
        f"Comando non riconosciuto: {cmd}\n"
        f"Uso: agent-skill [list|find|show|path] o skills-sync [apply|index|validate] [--migrate-legacy]",
        file=sys.stderr,
    )
    return 1


def _missing_skill_hint(mat: SkillMaterializer, name: str) -> str:
    """Dice perché la skill non c'è, distinguendo i due casi che contano."""
    manifest = skills_manifest(mat.vault_data)
    if not manifest.is_file():
        return (
            f"Skill '{name}' non disponibile: il manifest delle skill non esiste ancora "
            f"({manifest}). Esegui prima l'allineamento di questa macchina."
        )
    if name in mat.load_manifest():
        return (
            f"Skill '{name}' è dichiarata nel manifest ma non è ancora stata materializzata. "
            f"Esegui 'skills-sync apply'."
        )
    return f"Skill '{name}' non è dichiarata nel manifest delle skill."


if __name__ == "__main__":
    sys.exit(main())

