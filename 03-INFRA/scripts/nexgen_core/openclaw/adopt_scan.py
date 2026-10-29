"""What OpenClaw created and the canonical library does not know about yet, read-only.

OpenClaw is where the owner creates: the Skill Workshop learns a skill, ClawHub
installs one into the agent's workspace, a plugin publishes its own, and a
server added from its panel lands in `openclaw.json`. The engine keeps the
canonical library -- the skills manifest and the MCP manifest -- and
distributes it to every CLI, so a resource that exists only on the OpenClaw
side is invisible everywhere else.

One job: put a name on each of those resources. It reads, it never writes, so
the doctor (which only reports) and `nexgen openclaw adopt` (which acts) ask
the same code and cannot disagree about what is pending.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nexgen_core.config import load_mcp_manifest, load_skills_manifest
from nexgen_core.i18n import t
from nexgen_core.openclaw.config_file import read_config
from nexgen_core.openclaw.doctor_runtime import agent_path, workshop_dir
from nexgen_core.paths import mcp_manifest, skills_manifest

SKILL = "skill"
SERVER = "mcp"

#: The three folders a skill OpenClaw created can come from. Only the Workshop
#: archives its copy after an adoption; the other two are OpenClaw's own
#: install locations and stay where they are.
WORKSHOP = "workshop"
WORKSPACE = "workspace"
PLUGIN = "plugin"

#: OpenClaw's default agent, the one whose workspace is the state folder's own.
DEFAULT_AGENT_ID = "main"


@dataclass(frozen=True)
class WorkshopSkill:
    """One skill OpenClaw created, in one of the folders it writes.

    `source` says which folder: the Workshop learned it, ClawHub installed it
    into the agent workspace, or a plugin publishes it. The write path needs
    that: only a Workshop copy can be archived after the adoption.
    """

    name: str
    agent_id: str
    path: Path
    source: str = WORKSHOP


@dataclass(frozen=True)
class UnmanagedServer:
    """One `mcp.servers` entry the canonical manifest does not declare."""

    name: str
    spec: Mapping[str, Any]


@dataclass(frozen=True)
class Scan:
    """Everything waiting to be adopted, plus what stopped the scan from being sure."""

    skills: tuple[WorkshopSkill, ...] = ()
    servers: tuple[UnmanagedServer, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def total(self) -> int:
        return len(self.skills) + len(self.servers)


@dataclass(frozen=True)
class Library:
    """The two manifests the engine owns, or None where one cannot be read.

    A manifest that cannot be read is not an empty library: treating it as one
    would make every resource look unmanaged and invite the engine to adopt
    back what it already wrote.
    """

    skills: Mapping[str, Any] | None
    mcp: Mapping[str, Any] | None

    @property
    def complete(self) -> bool:
        return self.skills is not None and self.mcp is not None


def _read(path: Path, reader: Callable[..., dict[str, Any]]) -> dict[str, Any] | None:
    """One manifest, or None when it cannot be read: a finding, never a crash."""
    try:
        return reader(path)
    except Exception:  # noqa: BLE001 - nexgen: allow reason="an unreadable manifest is reported by the caller that needed it"
        return None


def library(vault_data: Path) -> Library:
    """Both manifests of the canonical library, read the way the engine reads them."""
    root = Path(vault_data)
    return Library(
        skills=_read(skills_manifest(root), load_skills_manifest),
        mcp=_read(mcp_manifest(root), load_mcp_manifest),
    )


def agent_entries(config: Mapping[str, Any] | None) -> dict[str, Any]:
    """The per-agent entries of `openclaw.json`, tolerating a file with no agents."""
    agents = config.get("agents") if isinstance(config, Mapping) else None
    if not isinstance(agents, Mapping):
        return {}
    entries = agents.get("entries")
    return dict(entries) if isinstance(entries, Mapping) else {}


def live_skill_folders(folder: Path) -> list[Path]:
    """The skills in one folder OpenClaw writes; `.archive` is history, not a live skill."""
    try:
        children = sorted(folder.iterdir())
    except OSError:  # nexgen: allow reason="a folder that cannot be read holds nothing this scan can report"
        return []
    return [child for child in children if child.is_dir() and not child.name.startswith(".")]


def workshop_skills(
    config: Mapping[str, Any] | None,
    state_dir: Path,
    home: Path,
    declared: set[str],
) -> list[WorkshopSkill]:
    """Every live learned skill whose name the skills manifest does not declare."""
    found: list[WorkshopSkill] = []
    for agent_id, entry in sorted(agent_entries(config).items(), key=lambda item: str(item[0])):
        folder = workshop_dir(Path(state_dir), Path(home), str(agent_id), entry)
        for child in live_skill_folders(folder):
            if child.name not in declared:
                found.append(WorkshopSkill(child.name, str(agent_id), child))
    return found


def agent_defaults(config: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """The `agents.defaults` table of `openclaw.json`, tolerating a file with none."""
    agents = config.get("agents") if isinstance(config, Mapping) else None
    defaults = agents.get("defaults") if isinstance(agents, Mapping) else None
    return defaults if isinstance(defaults, Mapping) else {}


def workspace_dir(
    state_dir: Path,
    home: Path,
    agent_id: str,
    entry: Any,
    defaults: Mapping[str, Any],
) -> Path:
    """This agent's working folder, whose `skills/` is where ClawHub installs.

    OpenClaw's own order: the agent's `workspace`, then `agents.defaults.workspace`
    -- with the agent id appended for every agent but the default one -- then the
    state folder's own `workspace` or `workspace-<id>`.
    """
    declared = entry.get("workspace") if isinstance(entry, Mapping) else None
    if isinstance(declared, str) and declared.strip():
        return agent_path(declared.strip(), Path(home), Path(state_dir))
    fallback = defaults.get("workspace")
    if isinstance(fallback, str) and fallback.strip():
        root = agent_path(fallback.strip(), Path(home), Path(state_dir))
        return root if str(agent_id) == DEFAULT_AGENT_ID else root / str(agent_id)
    return Path(state_dir) / ("workspace" if str(agent_id) == DEFAULT_AGENT_ID else f"workspace-{agent_id}")


def workspace_skills(
    config: Mapping[str, Any] | None,
    state_dir: Path,
    home: Path,
    declared: set[str],
) -> list[WorkshopSkill]:
    """Every live skill in an agent's workspace `skills/` folder.

    `openclaw skills install <slug>` copies the skill there and records its
    provenance in `<workspace>/.clawhub/lock.json`; a skill installed from a
    local folder lands in the same place. The engine never writes there, so a
    folder the manifest does not declare is the person's.
    """
    found: list[WorkshopSkill] = []
    defaults = agent_defaults(config)
    for agent_id, entry in sorted(agent_entries(config).items(), key=lambda item: str(item[0])):
        folder = workspace_dir(Path(state_dir), Path(home), str(agent_id), entry, defaults) / "skills"
        for child in live_skill_folders(folder):
            if child.name not in declared:
                found.append(WorkshopSkill(child.name, str(agent_id), child, WORKSPACE))
    return found


def _resolved(path: Path) -> Path | None:
    """One path with its links resolved, or None when it cannot be resolved."""
    try:
        return Path(path).resolve()
    except OSError:  # nexgen: allow reason="a link that cannot be resolved is skipped, not followed"
        return None


def plugin_skills(state_dir: Path, declared: set[str]) -> list[WorkshopSkill]:
    """The plugin skills a person installed, read from `<state>/plugin-skills`.

    OpenClaw owns that folder and fills it with one link per published plugin
    skill, pointing at the skill inside the plugin that brings it. A plugin
    bundled with the package publishes from inside the package, so only a link
    that resolves under `<state>/extensions` -- where a plugin the person
    installed lives -- is a candidate. A link that resolves anywhere else is
    skipped, never followed: the copy reads real bytes, and it reads them only
    from a folder this scan can name.
    """
    installed = _resolved(Path(state_dir) / "extensions")
    if installed is None:
        return []
    found: list[WorkshopSkill] = []
    for child in live_skill_folders(Path(state_dir) / "plugin-skills"):
        target = _resolved(child)
        if target is None or not target.is_relative_to(installed) or child.name in declared:
            continue
        found.append(WorkshopSkill(child.name, "", target, PLUGIN))
    return found


def one_per_name(skills: list[WorkshopSkill]) -> list[WorkshopSkill]:
    """The first candidate for each name, in the order the folders were read.

    OpenClaw loads one copy of a name, so a second one is the same skill from a
    lower-priority folder: adopting both would collide on the second.
    """
    seen: set[str] = set()
    kept: list[WorkshopSkill] = []
    for skill in skills:
        if skill.name in seen:
            continue
        seen.add(skill.name)
        kept.append(skill)
    return kept


def declared_names(manifest: Mapping[str, Any] | None) -> set[str]:
    """Every server name the engine owns: declared, retired, or a tombstone it keeps.

    A name the manifest knows is the engine's decision whatever its placement:
    a server the engine deliberately keeps off OpenClaw must not come back in
    through adoption, and a retired one must not be resurrected by a scan that
    finds its old entry still written in the file.
    """
    if not isinstance(manifest, Mapping):
        return set()
    servers = manifest.get("servers")
    names = {str(name) for name in servers} if isinstance(servers, Mapping) else set()
    retired = manifest.get("retired_servers")
    if isinstance(retired, list):
        names.update(str(name) for name in retired)
    return names


def unmanaged_servers(
    live: Mapping[str, Any] | None,
    manifest: Mapping[str, Any] | None,
) -> list[UnmanagedServer]:
    """The live `mcp.servers` entries the engine did not write, sorted by name.

    Keyed on the name, and the name is enough: `mcp_render.openclaw` writes a
    manifest key as itself, drops the retired ones, and leaves every other
    entry of the file alone. So anything the engine wrote carries a manifest
    name, everything else was written by a person or another tool, and there is
    no cycle to guard against: once adopted, the name is in the manifest and
    the next scan no longer sees it.
    """
    engine_owned = declared_names(manifest)
    return [
        UnmanagedServer(str(name), spec if isinstance(spec, Mapping) else {})
        for name, spec in sorted((live or {}).items())
        if str(name) not in engine_owned
    ]


def live_servers(config: Mapping[str, Any] | None) -> dict[str, Any]:
    """The `mcp.servers` table of a parsed `openclaw.json`, or an empty one."""
    mcp = config.get("mcp") if isinstance(config, Mapping) else None
    servers = mcp.get("servers") if isinstance(mcp, Mapping) else None
    return dict(servers) if isinstance(servers, Mapping) else {}


def scan(
    *,
    config_path: Path,
    state_dir: Path,
    home: Path,
    skills_manifest: Mapping[str, Any] | None,
    mcp_manifest: Mapping[str, Any] | None,
    config: Mapping[str, Any] | None = None,
) -> Scan:
    """Everything waiting to be adopted: `openclaw.json` read once, never written."""
    notes: list[str] = []
    loaded = dict(config) if isinstance(config, Mapping) else None
    if loaded is None:
        loaded, error = read_config(Path(config_path))
        if error:
            notes.append(t("openclaw.json is not readable ({error}): no server can be listed", error=error))
    if loaded is None:
        return Scan(notes=tuple(notes))
    declared = {
        str(name) for name in (skills_manifest or {}).get("skills", {})
    } if isinstance(skills_manifest, Mapping) else set()
    from nexgen_core.openclaw.adopt_plugins import scan_plugins

    engine_owned = declared_names(mcp_manifest)
    live = unmanaged_servers(live_servers(loaded), mcp_manifest)
    existing_names = engine_owned | {s.name for s in live}
    plugin_servers, plugin_notes = scan_plugins(Path(state_dir), loaded, existing_names)
    all_servers = sorted(list(live) + [s for s in plugin_servers if s.name not in existing_names], key=lambda s: s.name)
    all_notes = tuple(notes) + tuple(plugin_notes)
    return Scan(
        skills=tuple(one_per_name(
            workshop_skills(loaded, Path(state_dir), Path(home), declared)
            + workspace_skills(loaded, Path(state_dir), Path(home), declared)
            + plugin_skills(Path(state_dir), declared)
        )),
        servers=tuple(all_servers),
        notes=all_notes,
    )
