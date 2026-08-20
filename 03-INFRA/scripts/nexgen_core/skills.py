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
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.config import load_skills_manifest

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
    description: str = ""
    source_path: Path | None = None


def make_link_or_copy(src: Path, dst: Path) -> bool:
    """Crea un symlink (o copia su Windows se i privilegi symlink non sono attivi)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.is_symlink() or dst.is_file():
        try:
            if dst.is_symlink() and dst.resolve() == src.resolve():
                return False
            dst.unlink()
        except OSError:
            pass
    elif dst.is_dir():
        shutil.rmtree(dst, ignore_errors=True)

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
        _v = vault_data or Path(os.environ.get("AGENT_VAULT_DATA") or os.environ.get("KNOWLEDGE_VAULT_PATH") or str(self.home / "KnowledgeVault"))
        self.vault_data = _v
        self.engine_root = engine_root or Path(os.environ.get("AGENT_ENGINE_ROOT") or str(self.home / ".nexgen-engine" / "03-INFRA"))

        self.library_dir = self.home / ".agents" / "skill-library"
        self.active_dir = self.home / ".agents" / "skills"
        self.claude_dir = self.home / ".claude" / "skills"
        self.gemini_dir = self.home / ".gemini" / "antigravity-cli" / "skills"
        self.gemini_config_dir = self.home / ".gemini" / "config" / "skills"
        self.gemini_legacy_dir = self.home / ".gemini" / "skills"
        self.codex_dir = self.home / ".codex" / "skills"
        self.opencode_dir = self.home / ".opencode" / "skills"

    def load_manifest(self) -> dict[str, SkillEntry]:
        manifest_file = self.vault_data / "03-INFRA" / "agent-universal-layer" / "skills" / "skills.manifest.yaml"
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
                description=raw.get("description", ""),
            )
            # Risoluzione percorso sorgente
            if entry.origin == "vault":
                entry.source_path = self.vault_data / "03-INFRA" / "agent-universal-layer" / "skills" / name
            elif entry.origin == "engine":
                entry.source_path = self.engine_root / "agent-universal-layer" / "skills" / name
            skills[name] = entry

        return skills

    def materialize(self, apply: bool = True) -> tuple[int, list[str]]:
        """Materializza tutte le skill nella libreria e crea le viste native."""
        skills = self.load_manifest()
        actions: list[str] = []
        changes = 0

        self.library_dir.mkdir(parents=True, exist_ok=True)
        self.active_dir.mkdir(parents=True, exist_ok=True)

        for name, entry in skills.items():
            lib_dest = self.library_dir / name

            # Se la sorgente locale esiste, colleghiamo alla libreria
            if entry.source_path and entry.source_path.is_dir():
                if apply:
                    if make_link_or_copy(entry.source_path, lib_dest):
                        changes += 1
                        actions.append(f"Collegata skill '{name}' alla libreria")
            elif entry.origin == "github" and entry.repo and entry.commit:
                # Gestione repository github pinnato a commit immutabile
                cache_dir = self.home / ".agents" / "cache" / "github-skills" / name
                clone_success = cache_dir.is_dir()
                if apply and not clone_success:
                    try:
                        cache_dir.parent.mkdir(parents=True, exist_ok=True)
                        # Clona e posiziona sul commit esatto (senza shallow shallow-depth limitante)
                        res_clone = subprocess.run(
                            ["git", "clone", entry.repo, str(cache_dir)],
                            capture_output=True,
                            text=True,
                            check=False,
                        )
                        if res_clone.returncode == 0:
                            res_co = subprocess.run(
                                ["git", "-C", str(cache_dir), "checkout", entry.commit],
                                capture_output=True,
                                text=True,
                                check=False,
                            )
                            if res_co.returncode == 0:
                                clone_success = True
                            else:
                                actions.append(f"[ERRORE] Checkout commit {entry.commit} fallito per skill github '{name}': {res_co.stderr.strip()}")
                        else:
                            actions.append(f"[ERRORE] Clonazione fallita per skill github '{name}' ({entry.repo}): {res_clone.stderr.strip()}")
                    except Exception as exc:
                        actions.append(f"[ERRORE] Errore imprevisto nella clonazione di '{name}': {exc}")

                if clone_success and apply:
                    if make_link_or_copy(cache_dir, lib_dest):
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
        print("Uso: agent-skill [find|show] <argomenti>  oppure  skills-sync [apply|index] [--migrate-legacy]")
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
        if actions:
            for act in actions:
                print(f"  ✓ {act}")
        if flag_migrate:
            legacy_actions = mat.migrate_legacy(apply=True)
            if legacy_actions:
                for act in legacy_actions:
                    print(f"  ✓ {act}")
        print(f"Skill sincronizzate con successo ({changes} modifiche applicate).")
        return 0

    elif cmd == "index" or flag_index:
        idx = mat.generate_index()
        print(f"Indice generato in {idx}")
        return 0

    elif cmd == "find":
        query = positional[1].lower() if len(positional) > 1 else ""
        skills = mat.load_manifest()
        found = False
        for name, s in skills.items():
            if query in name.lower() or query in (s.description or "").lower():
                print(f"{name}: {s.description or '-'}")
                found = True
        return 0 if found else 1

    elif cmd == "show":
        if len(positional) < 2:
            print("Uso: agent-skill show <nome-skill>", file=sys.stderr)
            return 2
        name = positional[1]
        body_file = mat.library_dir / name / "SKILL.md"
        if not body_file.is_file():
            body_file = mat.library_dir / f"{name}.md"
        if body_file.is_file():
            print(body_file.read_text(encoding="utf-8"))
            return 0
        print(f"Skill '{name}' non trovata nella libreria locale ({mat.library_dir})", file=sys.stderr)
        return 1

    print(f"Comando non riconosciuto: {cmd}\nUso: agent-skill [find|show] o skills-sync [apply|index] [--migrate-legacy]", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())

