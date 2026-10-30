"""The agent workspaces: the folder, the manuals, the first-run file to remove.

One job: put each agent's manual where OpenClaw looks for it, and remove the
one file that would replace it. The rule that protects the person's work is
narrow on purpose: a file that already existed is never touched. The single
exception is a folder OpenClaw itself created a moment earlier during
onboarding — nothing in it can be the person's yet, and its `BOOTSTRAP.md` is
the first-run rite that would sit in front of the manual the engine writes.

An `AGENTS.md` the squad declares follows the same rule, with one extension: a
file the engine wrote is replaced, and carrying the mark is not the only way to
be one of those. A workspace prepared before the squad declared its own manual
holds exactly the manual this engine ships, mark and all missing, so that text is
the second fingerprint: byte equality proves nothing of the person's is in it.
The mark still says where the engine's text starts, and everything above it stays.

The org chart is not part of the manual: it is a second managed block the guard
writes from the roster, and the setup writes it too so a fresh machine does not
wait for the next sync to learn who reports to whom.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from nexgen_core.files import atomic_write_text
from nexgen_core.i18n import t
from nexgen_core.openclaw.squad import INSTRUCTION_MARK
from nexgen_core.openclaw.templates import render_workspace, shipped_manuals

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


def _split_block(text: str) -> tuple[str, str]:
    """The canonical-pointer block at the top of `text` (or ""), and the rest.

    The markers come from `guard_instructions`, imported here rather than spelled
    again: the sync cycle looks for that exact string, and a second spelling is
    how the block stops being found.
    """
    from nexgen_core.openclaw.org_block import OPENCLAW_BLOCK_BEGIN, OPENCLAW_BLOCK_END

    begin = text.find(OPENCLAW_BLOCK_BEGIN)
    end = text.find(OPENCLAW_BLOCK_END)
    if begin < 0 or end < begin:
        return "", text
    end += len(OPENCLAW_BLOCK_END)
    return text[begin:end], text[end:].lstrip("\r\n")


def _is_shipped(rest: str, shipped: Sequence[str]) -> bool:
    """Whether `rest` is exactly one of the manuals this engine ships for the agent.

    Line endings are the file's, not the engine's: the text on disk may be CRLF
    while the manual in memory is LF, and that difference is not the person's.
    """
    body = rest.replace("\r\n", "\n").strip()
    return bool(body) and any(body == manual.replace("\r\n", "\n").strip() for manual in shipped)


def _with_line_endings(text: str, sample: str) -> str:
    """`text` written with the line endings the file already uses."""
    if "\r\n" not in sample:
        return text
    return text.replace("\r\n", "\n").replace("\n", "\r\n")


def next_manual_text(existing: str, declared: str, shipped: Sequence[str] = ()) -> str | None:
    """The `AGENTS.md` the workspace should hold, or None when the file is the person's.

    A file carrying the mark is the engine's: the text from the mark down is what
    the squad last wrote, so it is replaced, and everything above it — where
    `nexgen sync` puts the block pointing at the canonical instructions — stays
    exactly as it is.

    A file without the mark is still the engine's when it is exactly the manual
    this engine ships for that agent, block on top or not. That is a workspace
    prepared before the squad declared instructions: every byte of it came from
    `render_workspace`, nothing of the person's can be in it, and treating it as
    theirs is how a squad change stopped reaching that workspace.

    Anything else is text only the person wrote, and is not ours to replace.
    """
    written = _with_line_endings(declared, existing)
    if not existing.strip():
        return written  # nothing on disk yet: the block this cycle writes is the engine's
    at = _existing_mark(existing)
    if at is not None:
        return existing[:at] + written
    head, rest = _split_block(existing)
    if not _is_shipped(rest, shipped):
        return None
    if not head:
        return written
    separator = "\r\n" if "\r\n" in head else "\n"
    return f"{head}{separator}{written}"


def write_instructions(
    target: Path,
    text: str,
    replace: bool,
    emit: Callable[[str], None],
    shipped: Sequence[str] = (),
) -> int:
    """Write the squad's `AGENTS.md`, and only where the engine owns the file.

    A file the engine never wrote is the person's, so it is reported and left
    alone; `next_manual_text` is the one place that decides which files those are.
    A fresh workspace (`replace`) was created by OpenClaw a moment ago, so there
    is nothing of the person's in it to keep.
    """
    existing = None
    if target.is_file():
        try:
            existing = target.read_text(encoding="utf-8")
        except OSError as exc:  # nexgen: allow reason="an unreadable file is reported instead of replaced"
            emit(t("Skipping {path}: it cannot be read ({error}).", path=target, error=exc))
            return 0
    if existing is not None and not replace:
        updated = next_manual_text(existing, text, shipped)
        if updated is None:
            emit(t("Skipping {path}: the engine did not write it, so it is yours and stays as it is.", path=target))
            return 0
        text = updated
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(target, text)
    return 1


def _agents_by_dir(dirs: Mapping[str, Path]) -> dict[Path, list[str]]:
    """The agents that share each workspace folder, in the order the proposal names them."""
    grouped: dict[Path, list[str]] = {}
    for agent_id, directory in dirs.items():
        grouped.setdefault(directory, []).append(agent_id)
    return grouped


def _write_org_chart(
    directory: Path,
    agents: Sequence[str],
    lang: str | None,
    roster: Sequence[str],
    reports_to: Mapping[str, str],
    emit: Callable[[str], None],
) -> None:
    """The chart block for one workspace, once per folder however many agents share it."""
    from nexgen_core.openclaw.org_block import align_openclaw_org_block

    action = align_openclaw_org_block(directory, agents, lang, roster, reports_to)
    if action:
        emit(action)


def _close_workspace(
    directory: Path,
    agent_id: str,
    written: int,
    fresh: bool,
    emit: Callable[[str], None],
) -> None:
    """Remove the first-run file of a fresh workspace and report what it received."""
    bootstrap = directory / BOOTSTRAP_FILE
    removed = fresh and bootstrap.is_file()
    if removed:
        bootstrap.unlink()
    emit(t(
        "{agent}: {path} ({written} manuals written, {bootstrap})",
        agent=agent_id,
        path=directory,
        written=written,
        bootstrap=t("first-run file removed") if removed else t("first-run file not present"),
    ))


def prepare(
    dirs: Mapping[str, Path],
    created: set[str] | Mapping[str, bool],
    lang: str | None,
    emit: Callable[[str], None],
    dry_run: bool = False,
    souls: Mapping[str, str] | None = None,
    instructions: Mapping[str, str] | None = None,
    roster: Sequence[str] | None = None,
    squad: Mapping[str, Any] | None = None,
) -> list[Path]:
    """Create each workspace, write the missing manuals, drop the first-run file.

    Returns the folders this run created. In `dry_run` it only says what it
    would do: no folder is made and no file is written. `roster` is the agents
    this machine has and `squad` is the one that declares who reports to whom:
    the org chart block written into each workspace says that instead of a fixed
    list of names, and the roster's own owner derives it from the squad.
    """
    from nexgen_core.openclaw.hierarchy import declared_leads

    grouped = _agents_by_dir(dirs)
    reports_to = declared_leads(squad)
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
            written += write_instructions(
                directory / INSTRUCTIONS_FILE, declared, fresh, emit, shipped_manuals(agent_id),
            )
        if grouped[directory][0] == agent_id:
            _write_org_chart(
                directory, grouped[directory], lang,
                tuple(dirs) if roster is None else roster, reports_to, emit,
            )
        if souls and agent_id in souls:
            soul_path = directory / "SOUL.md"
            # Always, because it is generated: a SOUL.md kept "only if missing" is a
            # second copy of the identity, and whoever edits it inside OpenClaw gets
            # an agent the other CLIs never see.
            atomic_write_text(soul_path, souls[agent_id])
            written += 1
        _close_workspace(directory, agent_id, written, fresh, emit)
    return made
