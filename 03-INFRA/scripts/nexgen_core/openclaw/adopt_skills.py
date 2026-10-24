"""Adopting one learned skill: into the Vault's own library, then into every CLI.

A skill the Workshop learned lives in OpenClaw's state folder, which no other
CLI reads and no backup carries. Adoption makes it canonical: the folder is
copied into `03-INFRA/agent-universal-layer/skills/<name>/` (where the engine
keeps `origin: vault` skills), one entry is appended to the skills manifest,
and the materializer builds the library and the native views for every target
the entry names. The copy in the Workshop is archived afterwards, through the
injected executor, so OpenClaw keeps loading the canonical one and the two
cannot diverge.

One job per function: this module owns the write path of a skill. What may be
adopted is `adopt_vetting`'s answer, and how the pass is ordered is `adopt`'s.
"""
from __future__ import annotations

import os
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from nexgen_core.files import atomic_write_text, backup_file
from nexgen_core.i18n import t
from nexgen_core.manifest_text import entry_span
from nexgen_core.mcp_placement import CLIS
from nexgen_core.openclaw.adopt_scan import WorkshopSkill
from nexgen_core.openclaw_processes import RunResult, default_runner
from nexgen_core.paths import resolve_engine_root, resolve_home, resolve_state_dir, resolve_vault_data, skills_manifest

#: The reason recorded in OpenClaw's own change feed for the archive.
ARCHIVE_REASON = "Adopted into the NeXgen canonical library"

#: `(argv) -> RunResult`: the executor a test replaces with a recorder. `which`
#: is injected too, so a machine without OpenClaw still has its adoption code
#: exercised instead of skipped.
Run = Callable[[list[str]], RunResult]
Which = Callable[[str], str | None]

#: What an adopted entry declares: the same five runtimes, in the order the
#: manifests already use, and `manual` so the catalog is the way in.
ADOPTED_TARGETS = list(CLIS)
ADOPTED_EXPOSURE = "manual"

#: A manifest that declares no skill yet, written as YAML's empty flow map: a
#: block key appended under `skills: {}` would land outside the document.
EMPTY_SKILLS = re.compile(r"(?m)^skills:[ \t]*\{[ \t]*\}[ \t]*$")


@dataclass(frozen=True)
class SkillAdoption:
    """What happened to one skill, in words a person can act on."""

    name: str
    adopted: bool
    note: str


def vault_skills_dir(vault_data: Path) -> Path:
    """Where the engine keeps the skills the Vault owns."""
    return Path(vault_data) / "03-INFRA" / "agent-universal-layer" / "skills"


def collision(manifest_text: str, name: str, destination: Path) -> str | None:
    """Why this name is taken, or None when it is free.

    A taken name is never overwritten: the entry in the manifest and the folder
    in the Vault may be someone's work, and the person is the one who decides
    which of the two deserves to keep the name. The manifest check reuses the
    one text reader that already knows a key may be quoted.
    """
    if destination.exists() or destination.is_symlink():
        return t("a skill named '{name}' is already in the Vault: nothing was overwritten", name=name)
    if entry_span(manifest_text, name) is not None:
        return t("a skill named '{name}' is already declared in the manifest", name=name)
    return None


def append_entry(
    text: str,
    name: str,
    description: str,
    targets: list[str],
    *,
    origin: str = "vault",
    openclaw_id: str | None = None,
) -> str:
    """The manifest text with one new entry, appended and leaving every other byte alone."""
    lines = [
        f"  {name}:",
        f"    origin: {origin}",
    ]
    if openclaw_id:
        lines.append(f"    openclaw_id: {openclaw_id}")
    lines.extend([
        f"    exposure: {ADOPTED_EXPOSURE}",
        f"    targets: [{', '.join(targets)}]",
    ])
    if description:
        quoted = description.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'    description: "{quoted}"')
    body = EMPTY_SKILLS.sub("skills:", text.rstrip("\n"))
    return body + "\n\n" + "\n".join(lines) + "\n"


def _tag_skill_frontmatter(path: Path, name: str) -> None:
    """Ensure SKILL.md records its OpenClaw provenance."""
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            fm = parts[1]
            tag = f"\norigin: openclaw\nopenclaw_id: {name}"
            if "openclaw" not in fm:
                path.write_text(f"---{tag}{fm}---{parts[2]}", encoding="utf-8")


def _copy_into_vault(source: Path, destination: Path) -> None:
    """Copy the learned folder where the library can point at it, all at once.

    The copy is built under a hidden sibling name and renamed into place: a
    half-copied skill is never visible to the materializer, and a failure
    leaves the Vault exactly as it was.
    """
    incoming = destination.with_name(f".adopt-{destination.name}-{os.getpid()}")
    if incoming.exists():
        shutil.rmtree(incoming, ignore_errors=True)
    try:
        shutil.copytree(source, incoming, symlinks=False)
        _tag_skill_frontmatter(incoming / "SKILL.md", destination.name)
        os.replace(incoming, destination)
    finally:
        if incoming.exists():
            shutil.rmtree(incoming, ignore_errors=True)


def _publish(manifest: Path, before: str, after: str, validate: Callable[[], list[str]]) -> str | None:
    """Publish the new manifest text, or say why it had to be put back."""
    if backup_file(manifest, tag="openclaw-adopt") is None:
        return t("the manifest cannot be backed up: nothing was written")
    atomic_write_text(manifest, after)
    problems = validate()
    if not problems:
        return None
    atomic_write_text(manifest, before)
    return t("the manifest would no longer validate ({problems}); put back as it was",
             problems="; ".join(problems[:3]))


def archive_learned(
    skill: WorkshopSkill,
    *,
    run: Run | None = None,
    which: Which | None = None,
) -> tuple[bool, str]:
    """Archive the Workshop copy so OpenClaw loads the canonical one.

    Run after the adoption, never before: if this fails the canonical skill
    still exists and every CLI sees it, while OpenClaw keeps its learned copy
    at a higher priority -- the one state a person has to be told about.
    """
    binary = (which or shutil.which)("openclaw")
    if binary is None:
        return False, t("`openclaw` is not on PATH: archive the learned copy by hand when it is")
    argv = [binary, "skills", "workshop", "archive", skill.name, "--reason", ARCHIVE_REASON]
    if skill.agent_id:
        argv.extend(["--agent", skill.agent_id])
    result = (run or default_runner)(argv)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        return False, t("OpenClaw refused the archive: {reason}", reason=detail[-1] if detail else "no detail")
    return True, t("archived in the Workshop, so OpenClaw loads the canonical copy")


def adopt_skill(
    skill: WorkshopSkill,
    description: str,
    *,
    home: Path | None = None,
    vault_data: Path | None = None,
    engine_root: Path | None = None,
    state_dir: Path | None = None,
    run: Run | None = None,
    which: Which | None = None,
) -> SkillAdoption:
    """Copy, declare, materialize and archive one learned skill."""
    from nexgen_core.lock import HostLock, host_mutation
    from nexgen_core.skills import SkillMaterializer

    resolved_home = resolve_home(home)
    vault = resolve_vault_data(resolved_home, vault_data)
    engine = resolve_engine_root(resolved_home, engine_root)
    state = resolve_state_dir(resolved_home, override=state_dir)
    manifest = skills_manifest(vault)
    destination = vault_skills_dir(vault) / skill.name
    if not manifest.is_file():
        return SkillAdoption(skill.name, False,
                             t("there is no skills manifest here: the Vault is not this machine's library"))
    before = manifest.read_text(encoding="utf-8")
    taken = collision(before, skill.name, destination)
    if taken:
        return SkillAdoption(skill.name, False, taken)

    materializer = SkillMaterializer(vault_data=vault, engine_root=engine, home=resolved_home)
    with HostLock(lock_path=state / "openclaw-adopt.lock", timeout=30, command_name="openclaw-adopt"), \
            host_mutation("openclaw-adopt", state_dir=state, timeout=30):
        try:
            _copy_into_vault(Path(skill.path), destination)
        except OSError as exc:
            return SkillAdoption(skill.name, False, t("the copy failed ({error}); nothing was written", error=exc))
        problem = _publish(
            manifest, before,
            append_entry(before, skill.name, description, ADOPTED_TARGETS, openclaw_id=skill.name),
            materializer.validate_manifest,
        )
        if problem:
            shutil.rmtree(destination, ignore_errors=True)
            return SkillAdoption(skill.name, False, problem)
        changes, _actions = materializer.materialize(apply=True)
    _archived, archive_note = archive_learned(skill, run=run, which=which)
    return SkillAdoption(
        skill.name, True,
        t("in the Vault and in the manifest ({changes} views written). OpenClaw: {note}",
          changes=changes, note=archive_note),
    )
