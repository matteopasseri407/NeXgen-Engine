"""The agent workspaces: the folder, the manuals, the first-run file to remove.

One job: put each agent's manual where OpenClaw looks for it, and remove the
one file that would replace it. The rule that protects the person's work is
narrow on purpose: a file that already existed is never touched. The single
exception is a folder OpenClaw itself created a moment earlier during
onboarding — nothing in it can be the person's yet, and its `BOOTSTRAP.md` is
the first-run rite that would sit in front of the manual the engine writes.

An `AGENTS.md` the squad declares follows the same rule, with one extension: a
file that carries the engine's mark was written by an earlier run, so replacing
it is how a squad change reaches the workspace, and the mark stays wherever it
already sits below an aligned instructions block.

Nothing here renders text: `templates.render_workspace` is the one place that
knows what a standard manual says, and `squad.get_squad_instructions` the one
place that reads what the squad declares. Both return files rather than writing
them.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from nexgen_core.files import atomic_write_text
from nexgen_core.i18n import t
from nexgen_core.openclaw.squad import INSTRUCTION_MARK
from nexgen_core.openclaw.templates import render_workspace

#: The first-run file OpenClaw leaves in a new workspace. It starts a
#: conversation that redefines the agent, which is exactly what must not happen
#: after the engine has written the manual.
BOOTSTRAP_FILE = "BOOTSTRAP.md"

#: The one manual the squad can supply itself, and the name OpenClaw reads.
INSTRUCTIONS_FILE = "AGENTS.md"


def agent_dirs(entries: Mapping[str, Any]) -> dict[str, Path]:
    """Each agent's workspace, as the proposal names it."""
    dirs: dict[str, Path] = {}
    for agent_id, entry in entries.items():
        workspace = entry.get("workspace") if isinstance(entry, dict) else None
        if isinstance(workspace, str) and workspace.strip():
            dirs[str(agent_id)] = Path(workspace)
    return dirs


def snapshot_dirs(state_dir: Path) -> set[str]:
    """The folder names under the state directory before onboarding runs."""
    if not state_dir.is_dir():
        return set()
    return {child.name for child in state_dir.iterdir() if child.is_dir()}


def created_by_openclaw(path: Path, state_dir: Path, before: set[str]) -> bool:
    """Whether onboarding itself created this workspace, just now.

    Only a direct child of the state directory can be judged: a workspace
    somewhere else was either there before or belongs to the person, and in
    both cases it is treated as theirs.
    """
    try:
        inside = path.parent.resolve() == state_dir.resolve()
    except OSError:  # nexgen: allow reason="an unresolvable path is treated as the person's, never as ours to overwrite"
        return False
    return inside and path.name not in before


def _write_manuals(
    directory: Path,
    agent_id: str,
    lang: str | None,
    replace: bool,
    emit: Callable[[str], None],
    omit: str | None = None,
) -> int:
    """Write the manuals that are missing; a file already there stays byte for byte."""
    try:
        files = render_workspace(agent_id, lang)
    except ValueError as exc:
        emit(t("Skipping the manual for {agent}: {error}", agent=agent_id, error=exc))
        return 0
    written = 0
    for name, text in files.items():
        if name == omit:
            continue
        target = directory / name
        if target.exists() and not replace:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(target, text)
        written += 1
    return written


def _existing_mark(text: str) -> int | None:
    """Where the engine's mark sits in a file, or None when the file is the person's."""
    at = text.find(INSTRUCTION_MARK)
    return None if at < 0 else at


def write_instructions(
    target: Path,
    text: str,
    replace: bool,
    emit: Callable[[str], None],
) -> int:
    """Write the squad's `AGENTS.md`, and only where the engine owns the file.

    A file the engine never marked is the person's, so it is reported and left
    alone. A marked file is replaced, keeping whatever sits above the mark: that
    is where `nexgen sync` puts the block pointing at the canonical instructions,
    and moving the block out would make the next sync rewrite the file again.
    """
    existing = None
    if target.is_file():
        try:
            existing = target.read_text(encoding="utf-8")
        except OSError as exc:  # nexgen: allow reason="an unreadable file is reported instead of replaced"
            emit(t("Skipping {path}: it cannot be read ({error}).", path=target, error=exc))
            return 0
    if existing is not None and not replace:
        at = _existing_mark(existing)
        if at is None:
            emit(t("Skipping {path}: it carries no engine mark, so it is yours and stays as it is.", path=target))
            return 0
        text = existing[:at] + text
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(target, text)
    return 1


def prepare(
    dirs: Mapping[str, Path],
    created: set[str] | Mapping[str, bool],
    lang: str | None,
    emit: Callable[[str], None],
    dry_run: bool = False,
    souls: Mapping[str, str] | None = None,
    instructions: Mapping[str, str] | None = None,
) -> list[Path]:
    """Create each workspace, write the missing manuals, drop the first-run file.

    Returns the folders this run created. In `dry_run` it only says what it
    would do: no folder is made and no file is written.
    """
    made: list[Path] = []
    for agent_id, directory in dirs.items():
        fresh = created.get(agent_id, False) if isinstance(created, Mapping) else agent_id in created
        if dry_run:
            emit(t("Would prepare the workspace for {agent}: {path}", agent=agent_id, path=directory))
            continue
        missing = not directory.is_dir()
        directory.mkdir(parents=True, exist_ok=True)
        if missing:
            made.append(directory)
        declared = (instructions or {}).get(agent_id)
        written = _write_manuals(
            directory, agent_id, lang, replace=fresh, emit=emit,
            omit=INSTRUCTIONS_FILE if declared else None,
        )
        if declared:
            written += write_instructions(directory / INSTRUCTIONS_FILE, declared, fresh, emit)
        if souls and agent_id in souls:
            soul_path = directory / "SOUL.md"
            if fresh or not soul_path.exists():
                atomic_write_text(soul_path, souls[agent_id])
                written += 1
        bootstrap = directory / BOOTSTRAP_FILE
        removed = False
        if fresh and bootstrap.is_file():
            bootstrap.unlink()
            removed = True
        emit(t(
            "{agent}: {path} ({written} manuals written, {bootstrap})",
            agent=agent_id,
            path=directory,
            written=written,
            bootstrap=t("first-run file removed") if removed else t("first-run file not present"),
        ))
    return made
