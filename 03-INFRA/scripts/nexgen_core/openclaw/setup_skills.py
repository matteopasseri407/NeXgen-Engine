# nexgen: one-job reason="copy the skills a squad agent declares into its workspace, with the index that lists them"
"""The skills an agent declares in the squad, as files in its own workspace.

`skills: [vault-save, nexgen-doctor]` on an agent puts each body at
`<workspace>/skills/<name>.md` and rewrites `<workspace>/skills/INDICE.md`, one
line per skill: name, one-line description, file path. The body comes from the
engine's `agent-universal-layer/skills/<name>/SKILL.md`, the vault copy first.

The ownership rule is the workspace's: a file the engine wrote carries the mark
on its first line and is rewritten; a file without it is the person's and stays.
Nothing is deleted: a skill the squad stops declaring keeps its file and only
leaves the index.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path

from nexgen_core.files import atomic_write_text
from nexgen_core.i18n import t

#: The folder inside the workspace, and the index file in it.
SKILLS_DIR = "skills"
INDEX_FILE = "INDICE.md"

#: The first line of every file this module writes: the proof the engine owns it.
SKILL_MARK = "<!-- nexgen:squad-skill"

#: A skill name is a folder name, never a path: no separator, no leading dot.
_NAME = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_DESCRIPTION = re.compile(r"^description:\s*(.+?)\s*$", re.MULTILINE)


def skill_roots(vault_data: Path | None, engine_root: Path | None) -> list[Path]:
    """Where a skill's `SKILL.md` is looked for, the vault copy first."""
    roots: list[Path] = []
    if isinstance(vault_data, Path):
        roots.append(vault_data / "03-INFRA" / "agent-universal-layer" / "skills")
    if isinstance(engine_root, Path):
        roots.append(engine_root / "03-INFRA" / "agent-universal-layer" / "skills")
        roots.append(engine_root / "agent-universal-layer" / "skills")
    roots.append(Path(__file__).resolve().parents[3] / "agent-universal-layer" / "skills")
    return roots


def find_skill(name: str, roots: Iterable[Path]) -> Path | None:
    """The first `SKILL.md` for `name`, or None when no root has it."""
    for root in roots:
        candidate = root / name / "SKILL.md"
        if candidate.is_file():
            return candidate
    return None


def split_skill(text: str) -> tuple[str, str]:
    """The one-line description from the frontmatter, and the body without it."""
    match = _FRONTMATTER.match(text)
    if not match:
        return "", text
    found = _DESCRIPTION.search(match.group(1))
    description = found.group(1).strip().strip("\"'") if found else ""
    return " ".join(description.split()), text[match.end():].lstrip("\n")


def _engine_owns(target: Path) -> bool:
    """A missing file, or one whose first line is the mark."""
    if not target.exists():
        return True
    try:
        with target.open(encoding="utf-8") as handle:
            return handle.readline().startswith(SKILL_MARK)
    except (OSError, UnicodeDecodeError):  # nexgen: allow reason="an unreadable file is treated as the person's and left alone"
        return False


def _write_owned(target: Path, text: str, emit: Callable[[str], None]) -> bool:
    if not _engine_owns(target):
        emit(t("Skipping {path}: the engine did not write it, so it is yours and stays as it is.", path=target))
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(target, text)
    return True


def index_text(rows: Sequence[tuple[str, str, Path]]) -> str:
    """`INDICE.md`: one line per skill, in the order the squad declares them."""
    lines = [
        f"{SKILL_MARK} generated from squad.yaml; edit the squad, not this file -->",
        "# Skill",
        "",
    ]
    for name, description, path in rows:
        lines.append(f"- {name}: {description or '-'} ({path})")
    return "\n".join(lines) + "\n"


def write_skills(
    directory: Path,
    names: Sequence[str],
    roots: Sequence[Path],
    emit: Callable[[str], None],
) -> int:
    """Materialize the declared skills in one workspace; returns the files written."""
    folder = directory / SKILLS_DIR
    rows: list[tuple[str, str, Path]] = []
    written = 0
    for name in names:
        if not _NAME.match(name):
            emit(t("Skipping the skill {name}: a skill name cannot be a path.", name=name))
            continue
        source = find_skill(name, roots)
        if source is None:
            emit(t("Skipping the skill {name}: no SKILL.md for it in the engine.", name=name))
            continue
        try:
            description, body = split_skill(source.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:  # nexgen: allow reason="an unreadable source is reported and left out of the index"
            emit(t("Skipping {path}: it cannot be read ({error}).", path=source, error=exc))
            continue
        target = folder / f"{name}.md"
        header = f"{SKILL_MARK} copied from {source}; edit the source, not this copy -->\n"
        if _write_owned(target, header + body, emit):
            written += 1
        rows.append((name, description, target))
    if _write_owned(folder / INDEX_FILE, index_text(rows), emit):
        written += 1
    return written


def prepare_skills(
    dirs: Mapping[str, Path],
    skills: Mapping[str, Sequence[str]],
    roots: Sequence[Path],
    emit: Callable[[str], None],
) -> None:
    """Each agent that declares `skills` gets them; an agent without the field is not touched."""
    for agent_id, names in skills.items():
        directory = dirs.get(agent_id)
        if directory is None:
            continue
        written = write_skills(directory, names, roots, emit)
        emit(t("{agent}: {written} skill files written", agent=agent_id, written=written))
