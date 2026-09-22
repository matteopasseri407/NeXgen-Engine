#!/usr/bin/env python3
"""Skill materialization and sync for NeXgen Engine v2.

Handles the 4 skill origins:
1. vault: owned by the user, carried by Git in the private data.
2. engine: owned by the product, read from the installed engine without duplication.
3. github: third-party, pinned to an immutable commit and cloned/fetched.
4. installer: third-party with a dedicated installer, pinned to a version.

Maintains the non-discovered library (~/.agents/skill-library/) and the native views for the 4 CLIs.
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import sys
from pathlib import Path

import yaml

SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.config import (  # noqa: E402
    SKILL_EXPOSURES,
    SKILL_ORIGINS,
    SKILL_TARGETS,
    load_skills_manifest,
)
from nexgen_core.i18n import t  # noqa: E402
from nexgen_core.paths import resolve_engine_root, resolve_home, resolve_vault_data, skills_manifest  # noqa: E402
from nexgen_core.skill_sources import (  # noqa: E402
    COMMIT_SHA_RE,
    SkillEntry,
    SkillFetcher,
    clone_url as clone_url,
    is_safe_skill_name,
    make_link_or_copy,
    same_tree_content,
)

IS_WINDOWS = platform.system() == "Windows"


def _extract_frontmatter_description(path: Path) -> str:
    """Extracts the description field from YAML frontmatter in a skill file."""
    if not path.is_file():
        return ""
    try:
        content = path.read_text(encoding="utf-8")
        match = re.match(r"\A---\s*\n(.*?)\n---\s*\n?", content, re.DOTALL)
        if match:
            loaded = yaml.safe_load(match.group(1))
            if isinstance(loaded, dict) and loaded.get("description"):
                desc = str(loaded["description"]).strip().replace("\n", " ")
                return " ".join(desc.split())
    except (OSError, yaml.YAMLError, UnicodeDecodeError, ValueError, TypeError, AttributeError):
        return ""
    return ""


class SkillMaterializer:
    """Materializes the skill library and generates the native views for the CLIs."""

    def __init__(
        self,
        vault_data: Path | None = None,
        engine_root: Path | None = None,
        home: Path | None = None,
    ) -> None:
        self.home = resolve_home(home)
        _v = resolve_vault_data(self.home, vault_data)
        self.vault_data = _v
        self.engine_root = resolve_engine_root(self.home, engine_root)
        self.fetcher = SkillFetcher(home=self.home)

        self.library_dir = self.home / ".agents" / "skill-library"
        self.active_dir = self.home / ".agents" / "skills"
        self.claude_dir = self.home / ".claude" / "skills"
        self.gemini_dir = self.home / ".gemini" / "antigravity-cli" / "skills"
        self.gemini_config_dir = self.home / ".gemini" / "config" / "skills"
        self.gemini_legacy_dir = self.home / ".gemini" / "skills"
        self.codex_dir = self.home / ".codex" / "skills"
        self.opencode_dir = self.home / ".opencode" / "skills"
        # OpenCode V2 discovers `~/.config/opencode/skills` natively (plus,
        # for compatibility, `~/.agents/skills` and `~/.claude/skills`). The
        # legacy `~/.opencode/skills` stays as a view target so machines
        # that still carry it keep working, but new views land native.
        self.opencode_native_dir = self.home / ".config" / "opencode" / "skills"

        #: Le cartelle da cui i runtime scoprono le skill da soli. Un
        #: installer di terze parti ci lascia la propria copia, e da lì viene
        #: caricata sempre: è il contrario di pigra. Il motore la sposta.
        self.dirs_by_target: dict[str, tuple[Path, ...]] = {
            "claude": (self.claude_dir,),
            "antigravity": (self.gemini_dir, self.gemini_config_dir, self.gemini_legacy_dir),
            "codex": (self.codex_dir,),
            "opencode": (self.active_dir, self.opencode_dir, self.opencode_native_dir),
        }

        self.discovery_dirs = (
            self.active_dir, self.claude_dir, self.gemini_dir,
            self.gemini_config_dir, self.gemini_legacy_dir,
            self.codex_dir, self.opencode_dir, self.opencode_native_dir,
        )

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
                install=list(raw.get("install") or []),
                path=raw.get("path") or raw.get("sub"),
                description=raw.get("description", ""),
                deps=raw.get("deps") if isinstance(raw.get("deps"), dict) else None,
            )
            # Source path resolution
            if entry.origin == "vault":
                entry.source_path = self.vault_data / "03-INFRA" / "agent-universal-layer" / "skills" / name
            elif entry.origin == "engine":
                entry.source_path = self.engine_root / "agent-universal-layer" / "skills" / name

            # Fallback to SKILL.md frontmatter description when not explicitly declared in manifest
            if not entry.description:
                for candidate in (
                    self.library_dir / name / "SKILL.md",
                    self.library_dir / f"{name}.md",
                    (entry.source_path / "SKILL.md") if entry.source_path else None,
                    entry.source_path if entry.source_path and entry.source_path.suffix == ".md" else None,
                ):
                    if candidate and candidate.is_file():
                        desc = _extract_frontmatter_description(candidate)
                        if desc:
                            entry.description = desc
                            break

            skills[name] = entry

        return skills

    def _belongs_here(self, entry: SkillEntry) -> bool:
        """Does this skill concern whoever is using this machine?

        `scope` and `owner` used to be read from the manifest and applied to
        nothing: a field you can set that has no effect is worse than a
        missing field, because it makes you think something was decided.

        On a single-operator install — no `AGENT_TEAM_MEMBER` declared —
        everything belongs to whoever is there, and nothing gets skipped.
        """
        if entry.scope != "personal":
            return True
        member = os.environ.get("AGENT_TEAM_MEMBER", "").strip()
        if not member:
            return True
        return entry.owner is None or entry.owner == member

    def validate_manifest(self) -> list[str]:
        """Checks the manifest without writing anything, and says what's wrong.

        Meant to catch an error before the guard cycle runs into it: a skill
        declared without a source, a pin that isn't a commit, a name that
        can't become a folder.
        """
        problems: list[str] = []
        manifest_file = skills_manifest(self.vault_data)
        if not manifest_file.is_file():
            return [t("The skills manifest doesn't exist: {path}", path=manifest_file)]

        for name, entry in self.load_manifest().items():
            if not is_safe_skill_name(name):
                problems.append(t("'{name}': the name can't become a folder", name=name))
            if entry.origin not in SKILL_ORIGINS:
                problems.append(t("'{name}': unknown origin '{origin}'", name=name, origin=entry.origin))
            if entry.exposure not in SKILL_EXPOSURES:
                problems.append(t("'{name}': unknown exposure '{exposure}'", name=name, exposure=entry.exposure))
            unknown = [target for target in entry.targets if target not in SKILL_TARGETS]
            if unknown:
                problems.append(t("'{name}': unknown runtimes {runtimes}", name=name, runtimes=", ".join(unknown)))
            if entry.origin == "github":
                if not entry.repo:
                    problems.append(t("'{name}': github origin without 'repo'", name=name))
                if not entry.commit:
                    problems.append(t("'{name}': github origin without 'commit'", name=name))
                elif not COMMIT_SHA_RE.match(entry.commit):
                    problems.append(
                        t("'{name}': pin '{commit}' is not a full 40-character commit", name=name, commit=entry.commit)
                    )
            if entry.origin == "installer" and not entry.version:
                problems.append(t("'{name}': installer origin without 'version'", name=name))
            if entry.origin == "upstream":
                # Inventoried only: the upstream installer owns the bytes.
                # The deps: block is the contract the doctor verifies.
                if not entry.deps:
                    problems.append(t("'{name}': upstream origin without 'deps' (the pin the doctor verifies)", name=name))
                else:
                    try:
                        from nexgen_core.provision import validate_deps
                        validate_deps(entry.deps, name)
                    except Exception as exc:
                        problems.append(str(exc))
            if entry.origin in ("vault", "engine"):
                src = entry.source_path
                if src is None or not (src / "SKILL.md").is_file():
                    problems.append(t("'{name}': missing SKILL.md file under {path}", name=name, path=src))
        return problems

    def _target_of(self, directory: Path) -> str | None:
        """Which runtime reads this directory."""
        for target, dirs in self.dirs_by_target.items():
            if directory in dirs:
                return target
        return None

    def _remove_stale_views(self, skills: dict[str, SkillEntry]) -> list[str]:
        """Takes back a view that is no longer declared.

        Without this, a skill can be made eager but never made lazy again:
        the view stays for good, and the manifest stops describing reality.
        Only views the engine itself created are touched — a folder that does
        not come from the library is someone else's and stays where it is.
        """
        actions: list[str] = []
        for directory in self.discovery_dirs:
            if not directory.is_dir():
                continue
            for candidate in sorted(directory.iterdir()):
                name = candidate.name
                lib_source = self.library_dir / name
                if not lib_source.is_dir():
                    continue  # non è nostra: si segnala altrove, non si tocca
                entry = skills.get(name)
                wanted = (
                    entry is not None
                    and entry.exposure in ("eager", "core")
                    and self._target_of(directory) in entry.targets
                )
                if wanted:
                    continue
                ours = candidate.is_symlink() and candidate.resolve() == lib_source.resolve()
                if not ours and not same_tree_content(lib_source, candidate):
                    continue  # copia divergente: potrebbe contenere lavoro altrui
                try:
                    if candidate.is_symlink() or candidate.is_file():
                        candidate.unlink()
                    else:
                        shutil.rmtree(candidate)
                except OSError:
                    continue
                actions.append(t("Skill '{name}' is lazy again for {target}",
                                 name=name, target=self._target_of(directory) or directory.name))
        return actions

    def materialize(self, apply: bool = True) -> tuple[int, list[str]]:
        """Materializes every skill into the library and creates the native views."""
        skills = self.load_manifest()
        actions: list[str] = []
        changes = 0

        self.library_dir.mkdir(parents=True, exist_ok=True)
        self.active_dir.mkdir(parents=True, exist_ok=True)

        for name, entry in skills.items():
            if not self._belongs_here(entry):
                continue
            lib_dest = self.library_dir / name

            # If the local source exists, link it into the library
            if entry.source_path and entry.source_path.is_dir():
                if apply:
                    if make_link_or_copy(entry.source_path, lib_dest):
                        changes += 1
                        actions.append(t("Linked skill '{name}' into the library", name=name))
            elif entry.origin == "github" and entry.repo and entry.commit:
                cache_dir = self.home / ".agents" / "cache" / "github-skills" / name
                if not COMMIT_SHA_RE.match(entry.commit):
                    actions.append("[ERROR] " + t(
                        "github skill '{name}': pin '{commit}' is not a full "
                        "40-character commit, skipping the entry",
                        name=name, commit=entry.commit,
                    ))
                    continue

                clone_success = False
                if apply:
                    clone_success, problem = self.fetcher.ensure_github_checkout(cache_dir, entry)
                    if problem:
                        actions.append(problem)

                if clone_success and apply:
                    # The manifest can point at a subfolder of the repo. The
                    # boundary needs checking: a `path` that traverses
                    # upward would link something outside the clone.
                    source = cache_dir
                    if entry.path:
                        candidate = (cache_dir / entry.path).resolve()
                        if not candidate.is_relative_to(cache_dir.resolve()):
                            actions.append("[ERROR] " + t(
                                "github skill '{name}': path '{path}' "
                                "escapes the cloned repository, skipping the entry",
                                name=name, path=entry.path,
                            ))
                            continue
                        source = candidate
                    if make_link_or_copy(source, lib_dest):
                        changes += 1
                        actions.append(t("Linked github skill '{name}' into the library", name=name))

            elif entry.origin == "installer" and apply:
                installed, note = self.fetcher.install_third_party(entry, lib_dest, self.discovery_dirs)
                if note:
                    actions.append(note)
                    if installed:
                        changes += 1
                if not installed:
                    continue

            elif entry.origin == "upstream":
                # Inventoried only, by design: the upstream installer owns
                # the bytes (per-provider rendered variants, not
                # vendorizable). Nothing to materialize; the doctor
                # verifies the declared deps offline-safe.
                continue

            # Active view generation (if exposure == eager or core)
            if entry.exposure in ("eager", "core") and lib_dest.is_dir() and apply:
                for target in entry.targets:
                    target_dirs = list(self.dirs_by_target.get(target, ()))

                    for tdir in target_dirs:
                        dest = tdir / name
                        if make_link_or_copy(lib_dest, dest):
                            changes += 1
                            actions.append(t("Created active view '{name}' for {target}", name=name, target=target))

        if apply:
            actions.extend(self._remove_stale_views(skills))

        # Regenerates INDEX.md
        if apply:
            self.generate_index(skills)

        return changes, actions

    def generate_index(self, skills: dict[str, SkillEntry] | None = None) -> Path:
        """Generates the ~/.agents/skills/INDEX.md file with the index of every skill."""
        if skills is None:
            skills = self.load_manifest()

        self.active_dir.mkdir(parents=True, exist_ok=True)
        index_file = self.active_dir / "INDEX.md"

        lines = [
            "# NeXgen Engine Skill Catalog",
            "",
            "Every skill available in the system (loaded on-demand via `agent-skill find` or `agent-skill show`).",
            "",
            "| Skill | Origin | Exposure | Description |",
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
        """Quarantines legacy eager views outside the discovery roots.

        Port of skills-sync.py --migrate-legacy from the release: old
        installations put third-party skills directly under the discovery
        roots. The recurring guard must not delete or silently move them;
        --migrate-legacy preserves them in a local, non-indexed quarantine.
        """
        skills = self.load_manifest()
        actions: list[str] = []
        views = {
            "shared": self.active_dir,
            "codex": self.codex_dir,
            "claude": self.claude_dir,
            "opencode": (self.opencode_dir, self.opencode_native_dir),
        }
        legacy_root = self.library_dir / "legacy"
        for scope, roots in views.items():
            if isinstance(roots, Path):
                roots = (roots,)
            for root in roots:
                actions.extend(self._migrate_legacy_root(scope, root, skills, legacy_root, apply))
        return actions

    def _migrate_legacy_root(
        self,
        scope: str,
        root: Path,
        skills: dict[str, SkillEntry],
        legacy_root: Path,
        apply: bool,
    ) -> list[str]:
        """Quarantines one discovery root's unmanaged views (see
        :meth:`migrate_legacy`). Split out so every runtime root --
        including the two OpenCode ones -- shares the exact same rule."""
        actions: list[str] = []
        if not root.is_dir() or root.is_symlink():
            return actions
        for entry in sorted(root.iterdir()):
            if entry.name.startswith(".") or entry.name == "INDEX.md":
                continue
            body = entry / "SKILL.md" if entry.is_dir() else entry
            if not body.is_file():
                continue
            managed = self.library_dir / entry.name
            spec = skills.get(entry.name)
            # One rule per scope, mirroring what materialize() creates: a
            # native view exists only for eager/core skills naming that
            # runtime in targets (the shared catalog is the exposure-only
            # exception). The v2 port checked "claude" for the codex scope
            # too, so a legitimate codex view looked like a stray on every
            # explicit --migrate-legacy run.
            scope_target = {"claude": "claude", "codex": "codex", "opencode": "opencode"}.get(scope)
            expected = (
                (scope == "shared" and spec is not None and spec.exposure in ("core", "eager"))
                or (
                    scope_target is not None
                    and spec is not None
                    and spec.exposure in ("core", "eager")
                    and scope_target in (spec.targets or [])
                )
            )
            prefix = f"legacy/{scope}/{entry.name}"
            if expected and (managed.exists() or managed.is_symlink()):
                actions.append(t("{prefix}: managed view kept", prefix=prefix))
                continue
            destination = legacy_root / scope / entry.name
            if destination.exists() or destination.is_symlink():
                actions.append(t("{prefix}: destination already exists, view left untouched", prefix=prefix))
                continue
            if not apply:
                actions.append(t("{prefix}: would be quarantined outside the discovery roots", prefix=prefix))
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(entry), str(destination))
            actions.append(t("{prefix}: quarantined outside the discovery roots", prefix=prefix))
        return actions


def _safe_mark(mark: str, stream=sys.stdout) -> str:
    try:
        mark.encode(getattr(stream, "encoding", None) or "utf-8")
        return mark
    except (UnicodeEncodeError, TypeError):
        return "[OK]" if mark == "✓" else "[X]"


def main(argv: list[str] | None = None) -> int:
    """Entrypoint for `skills-sync` and `agent-skill`."""
    if argv is None:
        argv = sys.argv[1:]

    mat = SkillMaterializer()

    if not argv or argv[0] in ("-h", "--help"):
        print(t(
            "Usage: agent-skill [list|find|show|path] <arguments>\n"
            "       skills-sync [apply|index|validate] [--migrate-legacy]"
        ))
        return 0

    # Normalizes the release-style flags (--apply, --index, --migrate-legacy)
    # at any position, while also accepting the v2 positional form.
    argv_l = [a.lower() for a in argv]
    flag_apply = any(a in ("--apply", "--sync") for a in argv_l)
    flag_index = "--index" in argv_l
    flag_migrate = "--migrate-legacy" in argv_l
    positional = [a for a in argv if not a.startswith("-")]

    cmd = positional[0].lower() if positional else ""
    ok_mark = _safe_mark("✓")
    err_mark = _safe_mark("✗")

    if cmd in ("apply", "sync") or flag_apply:
        changes, actions = mat.materialize(apply=True)
        failed = [a for a in actions if a.startswith("[ERROR]")]
        for act in actions:
            print(f"  {err_mark if act.startswith('[ERROR]') else ok_mark} {act}")
        if flag_migrate:
            for act in mat.migrate_legacy(apply=True):
                print(f"  {ok_mark} {act}")
        if failed:
            print(
                t("{failed} of {total} skills were not synced.", failed=len(failed), total=changes + len(failed)),
                file=sys.stderr,
            )
            return 1
        print(t("Skills synced successfully ({count} changes applied).", count=changes))
        return 0

    elif cmd == "validate":
        problems = mat.validate_manifest()
        for problem in problems:
            print(f"  {_safe_mark('✗', sys.stderr)} {problem}", file=sys.stderr)
        if problems:
            print(t("Skills manifest: {count} problems.", count=len(problems)), file=sys.stderr)
            return 1
        print(t("Skills manifest: no problems."))
        return 0

    elif cmd == "index" or flag_index:
        idx = mat.generate_index()
        print(t("Index generated at {path}", path=idx))
        return 0

    elif cmd == "list":
        for name, s in sorted(mat.load_manifest().items()):
            print(f"{name}\t{s.description or '-'}")
        return 0

    elif cmd == "find":
        terms = [term.lower() for term in positional[1:] if term.strip()]
        if not terms:
            print(t("Usage: agent-skill find <term> [term...]"), file=sys.stderr)
            return 2
        found = False
        for name, s in sorted(mat.load_manifest().items()):
            haystack = f"{name} {s.description or ''}".lower()
            if all(term in haystack for term in terms):
                print(f"{name}\t{s.description or '-'}")
                found = True
        if not found:
            print(t("No managed skill matches: {terms}", terms=" ".join(terms)), file=sys.stderr)
            return 1
        return 0

    elif cmd in ("show", "path"):
        if len(positional) < 2:
            print(t("Usage: agent-skill {cmd} <skill-name>", cmd=cmd), file=sys.stderr)
            return 2
        name = positional[1]
        if not is_safe_skill_name(name):
            print(
                t(
                    "'{name}' is not a valid skill name: letters, digits, "
                    "dots, hyphens and underscores are allowed.",
                    name=name,
                ),
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
        t(
            "Unrecognized command: {cmd}\n"
            "Usage: agent-skill [list|find|show|path] or skills-sync [apply|index|validate] [--migrate-legacy]",
            cmd=cmd,
        ),
        file=sys.stderr,
    )
    return 1


def _missing_skill_hint(mat: SkillMaterializer, name: str) -> str:
    """Says why the skill is missing, distinguishing the two cases that matter."""
    manifest = skills_manifest(mat.vault_data)
    if not manifest.is_file():
        return t(
            "Skill '{name}' not available: the skills manifest doesn't exist yet "
            "({path}). Align this machine first.",
            name=name, path=manifest,
        )
    if name in mat.load_manifest():
        return t(
            "Skill '{name}' is declared in the manifest but hasn't been materialized yet. "
            "Run 'skills-sync apply'.",
            name=name,
        )
    return t("Skill '{name}' is not declared in the skills manifest.", name=name)


if __name__ == "__main__":
    sys.exit(main())

