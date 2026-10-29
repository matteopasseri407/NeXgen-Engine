"""`nexgen openclaw adopt`: what OpenClaw created, made canonical, with a person's consent.

The library is the single source every CLI receives, and OpenClaw is a door
into it: the Workshop learns a skill, ClawHub installs one, a plugin publishes
its own, and a person adds a server from its panel. All of them live only in
OpenClaw's own folders until they are adopted, which means the other four
runtimes have never heard of them and a lost state folder takes them with it.

This module is the order of the pass and the words a person reads. What may be
adopted is `adopt_vetting`'s answer, and the writing is `adopt_skills`' and
`adopt_mcp`'s. Nothing here adopts without consent: `--yes` only means "do not
ask", and a resource that needs a person is still left alone and named.

When the pass really added a server, `adopt_render` runs the engine's own render
so the other CLIs can use it now, not at the next scheduled pass.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nexgen_core.i18n import t
from nexgen_core.openclaw import adopt_render, adopt_scan
from nexgen_core.openclaw.adopt_mcp import adopt_server
from nexgen_core.openclaw.adopt_scan import SERVER, SKILL, UnmanagedServer, WorkshopSkill
from nexgen_core.openclaw.adopt_skills import adopt_skill
from nexgen_core.openclaw.adopt_vetting import (
    REVIEW,
    SAFE,
    Verdict,
    skill_description,
    vet_server,
    vet_skill,
)
from nexgen_core.openclaw.setup import ask_yes_no
from nexgen_core.openclaw_processes import RunResult
from nexgen_core.paths import (
    mcp_manifest,
    openclaw_config,
    resolve_engine_root,
    resolve_home,
    resolve_state_dir,
    resolve_vault_data,
    skills_manifest,
)

Emit = Callable[[str], None]
Confirm = Callable[[str], bool]
Run = Callable[[list[str]], RunResult]
Which = Callable[[str], str | None]


@dataclass(frozen=True)
class Pending:
    """One resource OpenClaw created, with the verdict and the candidate behind it."""

    kind: str
    name: str
    verdict: Verdict
    skill: WorkshopSkill | None = None
    server: UnmanagedServer | None = None

    @property
    def status(self) -> str:
        return self.verdict.status


@dataclass(frozen=True)
class Plan:
    """What waits to be adopted, and what stopped the scan from being sure."""

    pending: tuple[Pending, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def total(self) -> int:
        return len(self.pending)

    def with_status(self, status: str) -> tuple[Pending, ...]:
        return tuple(item for item in self.pending if item.status == status)


def _manifest_note(kind: str, path: Path) -> str:
    """Why nothing of one kind can be listed, in the words the person acting needs."""
    return t("the {kind} manifest cannot be read ({path}): nothing of that kind can be listed",
             kind=kind, path=path)


def plan_adoption(
    *,
    skills: bool = True,
    mcp: bool = True,
    include_workshop: bool = True,
    home: Path | None = None,
    vault_data: Path | None = None,
    config: Mapping[str, Any] | None = None,
    config_path: Path | None = None,
) -> Plan:
    """Everything waiting to be adopted, vetted once, in the order a person reads it.

    A manifest that cannot be read lists nothing at all instead of listing
    everything: without it the engine cannot tell its own resources from the
    person's, and adopting a resource back is worse than adopting none.

    `include_workshop` is the person's answer, not the scan's: the plain command
    leaves the skills the Workshop taught itself inside OpenClaw and says so,
    while `--skills` puts them on the table. The default is the explicit door,
    because a caller that names skills is already asking for them.
    """
    home_dir = resolve_home(home)
    vault = resolve_vault_data(home_dir, vault_data)
    known = adopt_scan.library(vault)
    notes: list[str] = []
    if skills and known.skills is None:
        notes.append(_manifest_note("skills", skills_manifest(vault)))
    if mcp and known.mcp is None:
        notes.append(_manifest_note("MCP", mcp_manifest(vault)))
    path = Path(config_path) if config_path is not None else openclaw_config(home_dir)
    found = adopt_scan.scan(
        config_path=path,
        state_dir=path.parent,
        home=home_dir,
        skills_manifest=known.skills,
        mcp_manifest=known.mcp,
        config=config,
    )
    pending: list[Pending] = []
    if skills and known.skills is not None:
        candidates = tuple(found.skills) if include_workshop else adopt_scan.automatic_skills(found.skills)
        pending.extend(Pending(SKILL, skill.name, vet_skill(skill), skill=skill) for skill in candidates)
    if mcp and known.mcp is not None:
        pending.extend(Pending(SERVER, server.name, vet_server(server), server=server) for server in found.servers)
    if skills and not include_workshop:
        hidden = [skill for skill in found.skills if skill.source not in adopt_scan.AUTOMATIC_SOURCES]
        notes.extend(_workshop_note(len(hidden)))
    return Plan(tuple(pending), tuple(notes) + found.notes)


def _workshop_note(count: int) -> list[str]:
    """The sentence that says where the Workshop's own skills went, when they are left out."""
    if count == 0:
        return []
    return [t("{count} skills the Workshop learned stay inside OpenClaw: "
              "'nexgen openclaw adopt --skills' offers them one by one.", count=count)]


def _verdict_phrase(item: Pending) -> str:
    """What the plan says about one resource, in the words of its own verdict."""
    if item.status == SAFE:
        return t("safe to adopt")
    if item.status == REVIEW:
        return t("needs a person first ({reasons})", reasons=item.verdict.joined())
    return t("cannot be adopted ({reasons})", reasons=item.verdict.joined())


def render_plan(plan: Plan) -> str:
    """The table a person reads before anything is written."""
    lines: list[str] = []
    if plan.total == 0:
        lines.append(t("Nothing to adopt: everything OpenClaw created that the engine carries is already "
                       "in the canonical library."))
    else:
        lines.append(t("{count} resources created in OpenClaw are not in the canonical library yet:",
                       count=plan.total))
        lines.extend(f"  {item.kind} {item.name}: {_verdict_phrase(item)}" for item in plan.pending)
    lines.extend(t("  note: {note}", note=note) for note in plan.notes)
    return "\n".join(lines)


def _left_alone(item: Pending) -> str:
    """One resource that is not adopted and why, in a single clause."""
    reason = t("needs a person first") if item.status == REVIEW else t("cannot be adopted")
    return f"{item.kind} {item.name} ({reason})"


def _review_question(item: Pending) -> str:
    """The question asked about one resource the checks did not clear on their own."""
    return t("Adopt the skill '{name}' too? It {reasons}", name=item.name, reasons=item.verdict.joined())


def _adopt_one(
    item: Pending,
    *,
    home: Path,
    vault: Path,
    engine: Path,
    state: Path,
    run: Run | None,
    which: Which | None,
) -> tuple[bool, str]:
    """Hand one resource to the module that owns its write path."""
    if item.kind == SKILL and item.skill is not None:
        skill = item.skill
        result = adopt_skill(
            skill, skill_description(Path(skill.path)),
            home=home, vault_data=vault, engine_root=engine, state_dir=state, run=run, which=which,
        )
        return result.adopted, result.note
    server = item.server or UnmanagedServer(item.name, {})
    outcome = adopt_server(server, home=home, vault_data=vault)
    return outcome.adopted, outcome.note


def _write_and_report(
    items: list[Pending],
    *,
    emit: Emit,
    home: Path,
    vault: Path,
    engine: Path,
    state: Path,
    run: Run | None,
    which: Which | None,
) -> tuple[int, int]:
    """Adopt each resource and say what happened; returns (failed, servers adopted)."""
    adopted = 0
    failed = 0
    servers = 0
    for item in items:
        ok, note = _adopt_one(item, home=home, vault=vault, engine=engine, state=state, run=run, which=which)
        if ok:
            adopted += 1
            if item.kind == SERVER:
                servers += 1
            emit(t("  + {kind} {name}: {note}", kind=item.kind, name=item.name, note=note))
        else:
            failed += 1
            emit(t("  ! {kind} {name}: {note}", kind=item.kind, name=item.name, note=note))
    if not adopted and not failed:
        emit(t("Nothing adopted."))
    return failed, servers


def _run_auto(
    *,
    skills: bool,
    mcp: bool,
    dry_run: bool,
    as_json: bool,
    home: Path | None,
    vault_data: Path | None,
    engine_root: Path | None,
    state_dir: Path | None,
    config_path: Path | None,
    out: Emit | None,
    run: Run | None,
    which: Which | None,
) -> int:
    """Execute the non-interactive automatic pass."""
    import json

    from nexgen_core.openclaw.adopt_auto import run_auto_adopt

    res = run_auto_adopt(
        skills=skills,
        mcp=mcp,
        dry_run=dry_run,
        home=home,
        vault_data=vault_data,
        engine_root=engine_root,
        state_dir=state_dir,
        config_path=config_path,
        out=(lambda s: None) if as_json else out,
        run=run,
        which=which,
    )
    if as_json:
        payload = {
            "ok": res.ok,
            "entered": res.entered,
            "exited": res.exited,
            "pending_review": res.pending_review,
            "notes": res.notes,
            "lines": res.lines,
        }
        (out or print)(json.dumps(payload, ensure_ascii=False))
    return 0 if res.ok else 1


def _agreed(
    plan: Plan,
    *,
    yes: bool,
    ask: Confirm,
    emit: Emit,
) -> list[Pending] | None:
    """What this person said yes to, or None when they refused the whole plan.

    `--yes` is consent for the safe ones and nothing more: a resource the checks
    left for a person is still asked about, one at a time. The refusal of the
    main question is a separate answer, not an empty list, so the pass stops
    before it starts rather than reporting everything as left alone.
    """
    ready = list(plan.with_status(SAFE))
    if yes:
        return ready
    if not ask(t("Adopt the resources above into the canonical library now?")):
        emit(t("Nothing adopted."))
        return None
    ready.extend(item for item in plan.with_status(REVIEW) if ask(_review_question(item)))
    return ready


def _run_interactive(
    *,
    skills: bool,
    mcp: bool,
    include_workshop: bool,
    dry_run: bool,
    yes: bool,
    home: Path | None,
    vault_data: Path | None,
    engine_root: Path | None,
    state_dir: Path | None,
    config_path: Path | None,
    out: Emit | None,
    run: Run | None,
    which: Which | None,
    confirm: Confirm | None,
) -> int:
    """Interactive adoption pass asking confirmation when needed."""
    emit = out or print
    home_dir = resolve_home(home)
    vault = resolve_vault_data(home_dir, vault_data)
    plan = plan_adoption(
        skills=skills, mcp=mcp, include_workshop=include_workshop,
        home=home_dir, vault_data=vault, config_path=config_path,
    )
    emit(render_plan(plan))
    if plan.total == 0:
        return 0
    if dry_run:
        emit(t("Dry run: nothing was written."))
        return 0

    ask = confirm or ask_yes_no
    ready = _agreed(plan, yes=yes, ask=ask, emit=emit)
    if ready is None:
        return 0

    engine = resolve_engine_root(home_dir, engine_root)
    failed, servers = _write_and_report(
        ready,
        emit=emit,
        home=home_dir,
        vault=vault,
        engine=engine,
        state=resolve_state_dir(home_dir, override=state_dir),
        run=run,
        which=which,
    )
    if servers:
        _rendered, line = adopt_render.render_adopted_servers(
            home=home_dir, vault_data=vault, engine_root=engine,
        )
        emit(line)
    left = [item for item in plan.pending if item not in ready]
    if left:
        emit(t("Left alone: {items}. A person decides; run 'nexgen openclaw adopt' to be asked.",
               items="; ".join(_left_alone(item) for item in left)))
    return 1 if failed else 0


def run_adopt(
    *,
    skills: bool = True,
    mcp: bool = True,
    include_workshop: bool = True,
    dry_run: bool = False,
    yes: bool = False,
    auto: bool = False,
    as_json: bool = False,
    home: Path | None = None,
    vault_data: Path | None = None,
    engine_root: Path | None = None,
    state_dir: Path | None = None,
    config_path: Path | None = None,
    out: Emit | None = None,
    run: Run | None = None,
    which: Which | None = None,
    confirm: Confirm | None = None,
) -> int:
    """Show the plan, ask once, adopt what may be adopted, and name what was not.

    Exit code 1 only when an adoption the person asked for failed: a refusal,
    a blocked resource and a resource left for a person are answers, not errors.

    `include_workshop` reaches only the interactive plan: the automatic pass is
    never a person, so it always leaves the Workshop's own skills alone.
    """
    if auto:
        return _run_auto(
            skills=skills, mcp=mcp, dry_run=dry_run, as_json=as_json, home=home,
            vault_data=vault_data, engine_root=engine_root, state_dir=state_dir,
            config_path=config_path, out=out, run=run, which=which,
        )
    return _run_interactive(
        skills=skills, mcp=mcp, include_workshop=include_workshop, dry_run=dry_run, yes=yes,
        home=home, vault_data=vault_data, engine_root=engine_root, state_dir=state_dir,
        config_path=config_path, out=out, run=run, which=which, confirm=confirm,
    )
