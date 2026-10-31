"""The managed blocks inside every OpenClaw agent workspace's `AGENTS.md`.

One job: keep the delimited blocks the engine owns in each workspace file -- the
pointer to the canonical instructions, the org chart the roster decides, and the
manual a squad declares -- correct, and read which workspaces the OpenClaw
config resolves. Why: OpenClaw loads that file itself, it belongs to the person,
and the guard, the setup and the doctor must compare and write the same bytes.
Only the delimiters and the text inside them are ever written; everything
outside is theirs and is never touched.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from nexgen_core.action_notes import WARN
from nexgen_core.files import atomic_write_text, symlink_write_target
from nexgen_core.i18n import t

#: Delimiters of the block the engine owns inside an OpenClaw agent workspace's
#: `AGENTS.md`. Everything outside them is the person's text and is never
#: rewritten; everything inside is regenerated from the canonical file's path.
OPENCLAW_BLOCK_BEGIN = "<!-- nexgen:instructions:begin -->"
OPENCLAW_BLOCK_END = "<!-- nexgen:instructions:end -->"

#: Delimiters of the second managed block: the org chart the engine derives from
#: the roster. It is a block of its own because the shipped manual is the same on
#: every machine, while the chart is not, and a workspace written before the chart
#: existed still has to receive it.
OPENCLAW_ORG_BEGIN = "<!-- nexgen:org:begin -->"
OPENCLAW_ORG_END = "<!-- nexgen:org:end -->"

#: The marker pair of each managed block, so one set of helpers serves both.
OPENCLAW_BLOCK_MARKERS = (OPENCLAW_BLOCK_BEGIN, OPENCLAW_BLOCK_END)
OPENCLAW_ORG_MARKERS = (OPENCLAW_ORG_BEGIN, OPENCLAW_ORG_END)


def openclaw_instruction_block(canon: Path) -> str:
    """The managed block: a pointer to the canonical file, nothing duplicated.

    OpenClaw loads the `AGENTS.md` of the agent's own working directory at every
    session and truncates it (`agents.defaults.bootstrapMaxChars`), which is why
    this block goes at the top of the file instead of the end.
    """
    return "\n".join((
        OPENCLAW_BLOCK_BEGIN,
        "# NeXgen Engine instructions",
        "",
        "Canonical instructions live at:",
        str(canon),
        "",
        "At session start, read and follow that file before acting: it is the single",
        "source, and copying its content here would only let the two drift apart.",
        "Everything outside this block belongs to you and is never rewritten by the engine.",
        OPENCLAW_BLOCK_END,
    ))


def openclaw_org_block(
    agents: Sequence[str],
    lang: str | None = None,
    roster: Sequence[str] = (),
    declared: Mapping[str, str] | None = None,
) -> str:
    """The org chart block for a workspace, delimited like the instructions one.

    The content comes from `hierarchy.render_org_block`: this function owns only
    the markers, so the guard, the setup and the doctor all compare the same text.
    """
    from nexgen_core.openclaw.hierarchy import render_org_block

    body = render_org_block(agents, lang, roster or agents, declared)
    return "\n".join((OPENCLAW_ORG_BEGIN, body, OPENCLAW_ORG_END))


def openclaw_squad_leads(vault_data: Path, home: Path) -> dict[str, str]:
    """The `reportsTo` the squad declares, read the way the setup reads it."""
    from nexgen_core.openclaw.hierarchy import declared_leads
    from nexgen_core.openclaw.squad import find_squad_file, load_squad

    return declared_leads(load_squad(find_squad_file(vault_data, home=home)))


def openclaw_block_state(
    text: str,
    block: str,
    markers: tuple[str, str] = OPENCLAW_BLOCK_MARKERS,
    *,
    at_top: bool = True,
) -> str:
    """How one managed block sits in `text`: ok, missing, different, or broken.

    A file with only one of the two markers is "broken" (a person edited inside
    the block, or a write was cut short): guessing where the block starts or
    ends there could delete their text, so the caller reports and leaves it.
    `at_top` is the block's own place: the instructions block must be first, the
    org chart is aligned anywhere because it belongs at the end of the file.
    """
    begin_marker, end_marker = markers
    begin = text.find(begin_marker)
    end = text.find(end_marker)
    if begin < 0 and end < 0:
        return "missing"
    if begin < 0 or end < 0 or end < begin:
        return "broken"
    if text[begin:end + len(end_marker)] != block:
        return "different"
    return "ok" if (begin == 0 or not at_top) else "different"


def with_openclaw_block(
    text: str,
    block: str,
    markers: tuple[str, str] = OPENCLAW_BLOCK_MARKERS,
    *,
    at_top: bool = True,
) -> str:
    """`text` with the managed block in its place and the person's text untouched.

    The instructions block belongs on top (`at_top`): OpenClaw truncates the file
    and that pointer must survive the truncation. The org chart belongs at the
    end, where it is read after the manual and does not push the manual down. A
    block found anywhere else is moved, not duplicated: one file, one of each.
    The separator follows the block's own line endings, so a CRLF file stays CRLF
    instead of ending up with two conventions in one file.
    """
    begin_marker, end_marker = markers
    sep = "\r\n" if "\r\n" in block else "\n"
    begin = text.find(begin_marker)
    end = text.find(end_marker)
    if 0 <= begin < end:
        after = end + len(end_marker)
        if not at_top:
            return text[:begin] + block + text[after:]
        rest = text[:begin] + text[after:]
        tail = rest[begin:]
        if tail.startswith("\r\n"):
            rest = rest[:begin] + rest[begin + 2:]
        elif tail.startswith("\n"):
            rest = rest[:begin] + rest[begin + 1:]
        return f"{block}{sep}{rest}"
    if not at_top:
        if not text:
            return f"{block}{sep}"
        between = "" if text.endswith("\n") else sep
        return f"{text}{between}{sep}{block}{sep}"
    return f"{block}{sep}{text}"


def openclaw_line_ending(sample: str) -> str:
    """The line ending a file already uses, so a new block doesn't mix two."""
    return "\r\n" if "\r\n" in sample else "\n"


def openclaw_file_state(
    text: str,
    block: str,
    markers: tuple[str, str] = OPENCLAW_BLOCK_MARKERS,
    *,
    at_top: bool = True,
) -> str:
    """`openclaw_block_state` for a file whose line endings are its own."""
    return openclaw_block_state(
        text, block.replace("\n", openclaw_line_ending(text)), markers, at_top=at_top,
    )


def _openclaw_path(value: str, home: Path, base: Path) -> Path:
    """A path from OpenClaw's own config, expanded against `home`.

    `~` is expanded by hand, not by `expanduser`: this file must resolve the
    same way for a test pointing at a temporary home as for the real machine,
    and `expanduser` reads the environment's home regardless.
    """
    if value == "~":
        return home
    if value.startswith(("~/", "~\\")):
        return home / value[2:]
    candidate = Path(value)
    return candidate if candidate.is_absolute() else base / candidate


def _openclaw_default_agent_id(defaults: dict, entries: dict) -> str | None:
    """The agent that owns the default workspace, as OpenClaw resolves it.

    `agents.defaults.systemAgent.agentId` records it once the roster is explicit
    (the squad's `default`, written by both the setup and the sync), and `main`
    is the legacy name; without either, no entry owns the default folder.
    """
    system = defaults.get("systemAgent")
    recorded = system.get("agentId") if isinstance(system, dict) else None
    if isinstance(recorded, str) and recorded.strip():
        return recorded.strip()
    return "main" if "main" in entries else None


def openclaw_workspace_owners(config_path: Path, home: Path) -> list[tuple[tuple[str, ...], Path]]:
    """Every workspace the OpenClaw config resolves, with the agents that live in it.

    The rule is OpenClaw's own (`resolveAgentWorkspaceDir`):
    `agents.entries.<id>.workspace` when an agent pins one; otherwise the default
    agent gets `agents.defaults.workspace`, or `<state dir>/workspace` when that
    key is absent; every other agent gets `<agents.defaults.workspace>/<id>`, or
    `<state dir>/workspace-<id>` when no default is declared.

    Reading every entry without a `workspace` as the default folder is what made
    two agents look like they shared one `AGENTS.md`, and the default agent's
    manual then stopped updating at all. Two agents may still name one folder on
    purpose, and then one `AGENTS.md` is all of it: the caller is told which ids
    share it rather than silently picking one. An unreadable config yields
    nothing: this step cannot fix it, and the MCP checks already name the file.
    """
    from nexgen_core.mcp_render import load_json_object

    try:
        data = load_json_object(config_path)
    except ValueError:  # nexgen: allow reason="a corrupt config has no workspaces to align; the MCP checks report it"
        return []
    agents = data.get("agents") if isinstance(data.get("agents"), dict) else {}
    defaults = agents.get("defaults") if isinstance(agents.get("defaults"), dict) else {}
    declared = defaults.get("workspace")
    pinned_default = isinstance(declared, str) and bool(declared.strip())
    state_dir = config_path.parent
    default_path = (
        _openclaw_path(declared, home, state_dir)
        if pinned_default
        else state_dir / "workspace"
    )
    entries = agents.get("entries") if isinstance(agents.get("entries"), dict) else {}
    owner = _openclaw_default_agent_id(defaults, entries)
    owners: dict[Path, list[str]] = {}
    for agent_id, agent in entries.items() or [(None, None)]:
        value = agent.get("workspace") if isinstance(agent, dict) else None
        if isinstance(value, str) and value.strip():
            path = _openclaw_path(value, home, state_dir)
        elif agent_id is None or owner is None or str(agent_id) == owner:
            path = default_path
        elif pinned_default:
            path = default_path / str(agent_id)
        else:
            path = state_dir / f"workspace-{agent_id}"
        ids = owners.setdefault(path, [])
        if agent_id is not None:
            ids.append(str(agent_id))
    return [(tuple(ids), path) for path, ids in owners.items()]


def openclaw_workspaces(config_path: Path, home: Path) -> list[Path]:
    """Every agent workspace the OpenClaw config declares, deduplicated."""
    return [path for _, path in openclaw_workspace_owners(config_path, home)]


def openclaw_squad_manuals(runner) -> dict[str, str]:
    """The manuals the squad declares, by agent id, read the way the setup reads them.

    `nexgen sync` writes them too: a person who edits `common_md` or one agent's
    `agents_md` and then runs only the sync must see the new text in the
    workspace, because that is the command they run without thinking about it.
    """
    from nexgen_core.openclaw.squad import find_squad_file, get_squad_instructions, load_squad

    squad_file = find_squad_file(runner.vault_data, home=runner.home)
    texts, _unreadable = get_squad_instructions(load_squad(squad_file), squad_file)
    return texts


def _with_squad_manual(
    target: Path,
    existing: str,
    agents: Sequence[str],
    declared: Mapping[str, str],
) -> tuple[str | None, str | None]:
    """The file with the squad's manual in it, or why the text was left as it is.

    One folder holds one `AGENTS.md`, so two agents that share a workspace and
    declare different manuals are a squad mistake with no safe answer: nothing is
    replaced and the caller says so.
    """
    from nexgen_core.openclaw.setup_agents import next_manual_text
    from nexgen_core.openclaw.templates import shipped_manuals

    texts = {declared[agent] for agent in agents if agent in declared}
    if not texts:
        return None, None
    if len(texts) > 1:
        return None, WARN + t(
            "OpenClaw instructions at {path}: the agents sharing this workspace declare "
            "different AGENTS.md files, so the manual was left as it is",
            path=target,
        )
    shipped = tuple(manual for agent in agents for manual in shipped_manuals(agent))
    return next_manual_text(existing, texts.pop(), shipped), None


def align_openclaw_instructions(runner) -> list[str]:
    """Puts the two managed blocks, and the squad's own manual, in every workspace.

    OpenClaw has no instruction pointer file of its own: it reads the
    `AGENTS.md` of the agent's working directory. That file is the person's, so
    only the delimited blocks are written, only when they are missing or
    different, and a copy is taken first. The org chart is the second block
    because it is the one thing in that file that depends on the roster: an
    install that already exists would never see it if only the setup wrote it.
    The squad's own manual is the third thing this cycle writes there, under the
    instruction block: the setup is not the only command a person runs after
    editing the squad, and a change that waits for the next setup is a change
    that looks ignored. A workspace folder that does not exist is left to the
    setup command that creates it.
    """
    from nexgen_core.paths import openclaw_config

    config_path = openclaw_config(runner.home)
    if not config_path.is_file():
        return []  # OpenClaw was never set up here: nothing points anywhere
    canon = runner.vault_data / "03-INFRA" / "agent-universal-layer" / "instructions" / "AGENTS.md"
    if not canon.is_file():
        return []
    block = openclaw_instruction_block(canon)
    declared = openclaw_squad_manuals(runner)
    owners = openclaw_workspace_owners(config_path, runner.home)
    roster = tuple(dict.fromkeys(agent for agents, _workspace in owners for agent in agents))
    leads = openclaw_squad_leads(runner.vault_data, runner.home)
    actions: list[str] = []
    for agents, workspace in owners:
        action = _align_openclaw_workspace(workspace, block, canon, agents, declared, roster, leads)
        if action:
            actions.append(action)
    return actions


def _read_workspace_manual(target: Path) -> tuple[str, OSError | None]:
    """The workspace `AGENTS.md` as it is, line endings included, or why not.

    `newline=""` because the person's line endings are read and written back as
    they are; text mode would turn a CRLF file into CRCRLF on a rewrite.
    """
    try:
        if not target.is_file():
            return "", None
        with target.open("r", encoding="utf-8", newline="") as handle:
            return handle.read(), None
    except OSError as exc:
        return "", exc


def _align_openclaw_workspace(
    workspace: Path,
    block: str,
    canon: Path,
    agents: Sequence[str] = (),
    declared: Mapping[str, str] | None = None,
    roster: Sequence[str] = (),
    leads: Mapping[str, str] | None = None,
) -> str | None:
    """One OpenClaw workspace: both blocks and the squad's manual, or why not.

    A dotfiles-managed `AGENTS.md` is a symlink into its repository: it is
    followed, not replaced, except when it points at the canonical file.
    """
    target = workspace / "AGENTS.md"
    if not workspace.is_dir():
        return None  # the setup command creates the folder; the check reports it
    write_target = symlink_write_target(target, keep=canon)
    existing, read_error = _read_workspace_manual(target)
    if read_error is not None:
        return WARN + t(
            "OpenClaw instructions at {path} left untouched ({error})", path=target, error=read_error,
        )
    state = openclaw_file_state(existing, block)
    if state == "broken":
        return WARN + t(
            "OpenClaw instructions at {path}: the managed block's markers are incomplete, left untouched",
            path=target,
        )
    org = _workspace_org_block(agents, existing, roster, leads or {})
    org_file = org.replace("\n", openclaw_line_ending(existing))
    if org and openclaw_block_state(existing, org_file, OPENCLAW_ORG_MARKERS, at_top=False) == "broken":
        return WARN + t(
            "OpenClaw instructions at {path}: the org chart block's markers are incomplete, left untouched",
            path=target,
        )
    updated, warning = _with_squad_manual(target, existing, agents, declared or {})
    source = existing if updated is None else updated
    ending = openclaw_line_ending(source)
    wanted = with_openclaw_block(source, block.replace("\n", ending))
    if org:
        wanted = with_openclaw_block(
            wanted, org.replace("\n", ending), OPENCLAW_ORG_MARKERS, at_top=False,
        )
    if wanted == existing:
        # Both blocks are aligned and the squad's text is already what it declares.
        return warning
    if target.is_file():
        from nexgen_core.files import backup_file

        try:
            backup_file(target, tag="instructions")
        except OSError as exc:
            return WARN + t(
                "OpenClaw instructions at {path} left untouched: safety backup failed ({error})",
                path=target, error=exc,
            )
    atomic_write_text(write_target, wanted, newline="")
    return t("OpenClaw instructions aligned in {path}", path=target)


def _workspace_org_block(
    agents: Sequence[str],
    existing: str,
    roster: Sequence[str],
    leads: Mapping[str, str],
) -> str:
    """The org chart for the agents of one workspace, in the manual's own language."""
    from nexgen_core.openclaw.hierarchy import manual_language

    if not agents:
        return ""
    return openclaw_org_block(agents, manual_language(existing), roster, leads)


def align_openclaw_org_block(
    workspace: Path,
    agents: Sequence[str],
    lang: str | None = None,
    roster: Sequence[str] = (),
    declared: Mapping[str, str] | None = None,
) -> str | None:
    """The org chart block alone, for a workspace the setup has just prepared.

    The guard writes it every cycle too. This exists so `nexgen openclaw setup`
    does not leave the chart out until the next sync, which on an existing
    install is the difference between the agent knowing its lead today or never.
    """
    from nexgen_core.openclaw.hierarchy import manual_language

    target = workspace / "AGENTS.md"
    if not agents or not target.is_file():
        return None
    existing, read_error = _read_workspace_manual(target)
    if read_error is not None:
        return WARN + t(
            "OpenClaw instructions at {path} left untouched ({error})", path=target, error=read_error,
        )
    block = openclaw_org_block(agents, manual_language(existing) or lang, roster, declared)
    file_block = block.replace("\n", openclaw_line_ending(existing))
    state = openclaw_block_state(existing, file_block, OPENCLAW_ORG_MARKERS, at_top=False)
    if state == "broken":
        return WARN + t(
            "OpenClaw instructions at {path}: the org chart block's markers are incomplete, left untouched",
            path=target,
        )
    wanted = with_openclaw_block(existing, file_block, OPENCLAW_ORG_MARKERS, at_top=False)
    if wanted == existing:
        return None
    from nexgen_core.files import backup_file

    try:
        backup_file(target, tag="instructions")
    except OSError as exc:
        return WARN + t(
            "OpenClaw instructions at {path} left untouched: safety backup failed ({error})",
            path=target, error=exc,
        )
    atomic_write_text(symlink_write_target(target, keep=target), wanted, newline="")
    return t("OpenClaw org chart aligned in {path}", path=target)
