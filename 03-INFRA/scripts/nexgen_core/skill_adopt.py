"""`nexgen skills adopt`: let the engine own the skills it ships, so an engine update updates them.

Older installs copied the engine's starter skills into the Vault and declared the copies `origin: vault`. A copy
is frozen the day it is made: the engine's next release fixes the skill and the copy keeps running the old text,
silently, because the Vault's copy is what gets linked into every CLI. `origin: engine` links the engine's own
folder instead, so the update is the only step.

What it changes, per skill: the entry's `origin` line (nothing else in the manifest, comments included) and the
vendored copy, which moves to a backup folder under the machine's state directory instead of being deleted. A copy
identical to the engine's is adopted without ceremony. A copy that differs holds either an old version or something
you wrote, and the command cannot tell which, so it is left alone unless you name it and pass `--force`.
"""
from __future__ import annotations

import difflib
import re
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from nexgen_core.files import atomic_write_text, backup_file
from nexgen_core.i18n import t
from nexgen_core.manifest_text import entry_span
from nexgen_core.paths import resolve_engine_root, resolve_home, resolve_state_dir, resolve_vault_data, skills_manifest
from nexgen_core.skill_sources import same_tree_content

SAME, DIFFERS = "same", "differs"

_ORIGIN_LINE = re.compile(r"^(\s+origin:\s*[\"']?)vault([\"']?\s*(?:#.*)?)$", re.MULTILINE)


@dataclass
class Candidate:
    name: str
    status: str  # SAME | DIFFERS
    shipped: Path
    copy: Path


@dataclass
class Outcome:
    name: str
    adopted: bool
    note: str


def shipped_skills(engine_root: Path) -> dict[str, Path]:
    root = Path(engine_root) / "agent-universal-layer" / "skills"
    if not root.is_dir():
        return {}
    return {p.name: p for p in sorted(root.iterdir()) if (p / "SKILL.md").is_file()}


def candidates(vault_data: Path, engine_root: Path) -> list[Candidate]:
    """Skills declared `origin: vault` that the engine also ships, and whether the copy still matches."""
    from nexgen_core.skills import SkillMaterializer

    shipped = shipped_skills(engine_root)
    found = []
    for name, entry in SkillMaterializer(vault_data=vault_data, engine_root=engine_root).load_manifest().items():
        if entry.origin != "vault" or name not in shipped or entry.source_path is None or not entry.source_path.is_dir():
            continue
        status = SAME if same_tree_content(shipped[name], entry.source_path) else DIFFERS
        found.append(Candidate(name, status, shipped[name], entry.source_path))
    return found


def describe_difference(candidate: Candidate) -> str:
    """One line on how a changed copy differs: which files, and how many lines of the main one."""
    ours = {p.relative_to(candidate.copy) for p in candidate.copy.rglob("*") if p.is_file()}
    theirs = {p.relative_to(candidate.shipped) for p in candidate.shipped.rglob("*") if p.is_file()}
    files = sorted(str(f) for f in ours ^ theirs)
    added = removed = 0
    for rel in sorted(ours & theirs):
        mine, engine = (candidate.copy / rel).read_bytes(), (candidate.shipped / rel).read_bytes()
        if mine == engine:
            continue
        files.append(str(rel))
        try:
            for line in difflib.unified_diff(mine.decode().splitlines(), engine.decode().splitlines(), lineterm="", n=0):
                if line.startswith("+") and not line.startswith("+++"):
                    added += 1
                elif line.startswith("-") and not line.startswith("---"):
                    removed += 1
        except UnicodeDecodeError:
            pass
    return t("differs in {files}; the engine's text has {added} lines yours lacks and yours has {removed} the engine's lacks",
             files=", ".join(files) or "?", added=added, removed=removed)


def _retarget(text: str, name: str) -> str | None:
    """The manifest text with this entry's origin switched to `engine`, or None if it cannot be done safely."""
    span = entry_span(text, name)
    if span is None:
        return None
    start, end = span
    block, count = _ORIGIN_LINE.subn(r"\1engine\2", text[start:end])
    if count != 1:
        return None
    return text[:start] + block + text[end:]


def adopt(
    names: list[str] | None = None,
    *,
    include_changed: bool = False,
    dry_run: bool = False,
    home: Path | None = None,
    vault_data: Path | None = None,
    engine_root: Path | None = None,
    state_dir: Path | None = None,
) -> list[Outcome]:
    """Adopts the named candidates (all identical ones when `names` is None). Changed ones need `include_changed`."""
    from nexgen_core.lock import HostLock, host_mutation
    from nexgen_core.skills import SkillMaterializer

    resolved_home = resolve_home(home)
    vault = resolve_vault_data(resolved_home, vault_data)
    engine = engine_root if engine_root is not None else resolve_engine_root(resolved_home)
    state = resolve_state_dir(resolved_home, override=state_dir)
    manifest = skills_manifest(vault)

    outcomes: list[Outcome] = []
    pool = {c.name: c for c in candidates(vault, engine)}
    chosen: list[Candidate] = []
    for name in names if names else [n for n, c in pool.items() if c.status == SAME]:
        candidate = pool.get(name)
        if candidate is None:
            outcomes.append(Outcome(name, False, t("not a skill the engine ships, or already followed from the engine")))
        elif candidate.status == DIFFERS and not include_changed:
            outcomes.append(Outcome(name, False, describe_difference(candidate) + ". " + t("Left alone: look first, then --force if the engine's is the one you want.")))
        else:
            chosen.append(candidate)
    if dry_run or not chosen:
        outcomes.extend(Outcome(c.name, False, t("would follow the engine (your copy kept in a backup folder)")) for c in chosen)
        return outcomes

    with HostLock(lock_path=state / "skill-adopt.lock", timeout=30, command_name="skill-adopt"), \
            host_mutation("skill-adopt", state_dir=state, timeout=30):
        before = manifest.read_text(encoding="utf-8")
        after = before
        edited: list[Candidate] = []
        for candidate in chosen:
            updated = _retarget(after, candidate.name)
            if updated is None:
                outcomes.append(Outcome(candidate.name, False, t("could not find a single `origin: vault` line in its manifest entry: edit it by hand")))
                continue
            after, edited = updated, [*edited, candidate]
        if not edited:
            return outcomes
        if backup_file(manifest, tag="adopt") is None:
            outcomes.append(Outcome("*", False, t("cannot back up the manifest, nothing written")))
            return outcomes
        atomic_write_text(manifest, after)
        problems = SkillMaterializer(vault_data=vault, engine_root=engine, home=resolved_home).validate_manifest()
        if problems:
            atomic_write_text(manifest, before)
            outcomes.append(Outcome("*", False, t("the manifest no longer validates ({problems}); put back as it was", problems="; ".join(problems[:3]))))
            return outcomes

        vault_stash = state / "skill-copies" / time.strftime("%Y%m%d-%H%M%S")
        for candidate in edited:
            vault_stash.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(candidate.copy), str(vault_stash / candidate.name))
            except OSError as exc:
                outcomes.append(Outcome(candidate.name, True, t("follows the engine now; its old copy could not be moved away ({error})", error=exc)))
                continue
            outcomes.append(Outcome(candidate.name, True, t("follows the engine now; your copy is in {where}", where=vault_stash / candidate.name)))
        SkillMaterializer(vault_data=vault, engine_root=engine, home=resolved_home).materialize(apply=True)
    return outcomes


def main(names: list[str], *, all_: bool, force: bool, dry_run: bool) -> int:
    resolved_home = resolve_home()
    vault = resolve_vault_data(resolved_home)
    engine = resolve_engine_root(resolved_home)
    found = candidates(vault, engine)
    if not found:
        print(t("Nothing to do: no skill in your Vault is a copy of one the engine ships."))
        return 0
    if not names and not all_:
        print(t("Skills the engine ships and your Vault keeps its own copy of (the copy runs, so it does not follow engine updates):"))
        for candidate in found:
            note = t("identical to the engine's") if candidate.status == SAME else describe_difference(candidate)
            print(f"  {candidate.name}: {note}")
        print(t("nexgen skills adopt --all  follows the engine for the identical ones; name a skill (with --force) for one that differs."))
        return 0
    if all_ and names:
        print(t("Name skills or use --all, not both."), file=sys.stderr)
        return 2
    outcomes = adopt(None if all_ and not force else (names or [c.name for c in found]), include_changed=force, dry_run=dry_run)
    failed = False
    for outcome in outcomes:
        mark = "✓" if outcome.adopted else "·"
        print(f"  {mark} {outcome.name}: {outcome.note}")
        failed = failed or outcome.name == "*"
    if any(o.adopted for o in outcomes):
        print(t("Commit and publish with 'nexgen vault push' so the other machines follow too."))
    return 1 if failed else 0
