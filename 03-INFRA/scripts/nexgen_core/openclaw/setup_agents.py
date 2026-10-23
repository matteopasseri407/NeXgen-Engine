"""The agent workspaces: the folder, the manuals, the first-run file to remove.

One job: put each agent's manual where OpenClaw looks for it, and remove the
one file that would replace it. The rule that protects the person's work is
narrow on purpose: a file that already existed is never touched. The single
exception is a folder OpenClaw itself created a moment earlier during
onboarding — nothing in it can be the person's yet, and its `BOOTSTRAP.md` is
the first-run rite that would sit in front of the manual the engine writes.

Nothing here renders text: `templates.render_workspace` is the one place that
knows what a manual says, and it returns files rather than writing them.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from nexgen_core.files import atomic_write_text
from nexgen_core.i18n import t
from nexgen_core.openclaw.templates import render_workspace

#: The first-run file OpenClaw leaves in a new workspace. It starts a
#: conversation that redefines the agent, which is exactly what must not happen
#: after the engine has written the manual.
BOOTSTRAP_FILE = "BOOTSTRAP.md"


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


def _write_manuals(directory: Path, agent_id: str, lang: str | None, replace: bool, emit: Callable[[str], None]) -> int:
    """Write the manuals that are missing; a file already there stays byte for byte."""
    try:
        files = render_workspace(agent_id, lang)
    except ValueError as exc:
        emit(t("Skipping the manual for {agent}: {error}", agent=agent_id, error=exc))
        return 0
    written = 0
    for name, text in files.items():
        target = directory / name
        if target.exists() and not replace:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(target, text)
        written += 1
    return written


def prepare(
    dirs: Mapping[str, Path],
    created: set[str] | Mapping[str, bool],
    lang: str | None,
    emit: Callable[[str], None],
    dry_run: bool = False,
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
        written = _write_manuals(directory, agent_id, lang, replace=fresh, emit=emit)
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
