"""The skill catalog page: one Markdown table over everything declared.

One job: turn the manifest entries into `~/.agents/skills/INDEX.md`, the page a
person (or an agent) reads to find out what exists without opening a body. It
used to be the last thirty lines of `SkillMaterializer`, which is how a
materializer came to own a page that has nothing to do with materializing
anything. The `Targets` column names the runtimes that get a native view, because
`~/.agents/skills` is the view of more than one and "does this skill reach
OpenClaw?" was otherwise unanswerable without reading the manifest.
"""
from __future__ import annotations

from pathlib import Path

from nexgen_core.files import atomic_write_text
from nexgen_core.skill_sources import SkillEntry

HEADER = "Every skill available in the system (loaded on-demand via `agent-skill find` or `agent-skill show`)."


def render_index(skills: dict[str, SkillEntry]) -> str:
    """The whole catalog as text: a table row per skill, sorted by name."""
    lines = [
        "# NeXgen Engine Skill Catalog",
        "",
        HEADER,
        "",
        "| Skill | Origin | Exposure | Targets | Description |",
        "|---|---|---|---|---|",
    ]
    for name in sorted(skills):
        entry = skills[name]
        lines.append(f"| `{name}` | `{entry.origin}` | `{entry.exposure}` | {_targets(entry)} | {entry.description or '-'} |")
    lines.append("")
    return "\n".join(lines)


def write_index(active_dir: Path, skills: dict[str, SkillEntry]) -> Path:
    """Writes the catalog into the shared skills folder and returns the file."""
    active_dir.mkdir(parents=True, exist_ok=True)
    index_file = active_dir / "INDEX.md"
    atomic_write_text(index_file, render_index(skills))
    return index_file


def _targets(entry: SkillEntry) -> str:
    """The runtimes this skill reaches, or `-` when the entry declares none."""
    declared = entry.targets if isinstance(entry.targets, list) else []
    return ", ".join(str(target) for target in declared) or "-"
