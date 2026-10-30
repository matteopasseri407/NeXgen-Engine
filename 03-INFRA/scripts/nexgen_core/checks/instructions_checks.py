"""Checks for the canonical instructions and AGENTS.md bootstrap hygiene.

Ported from the "Canonical instructions" and "Canonical bootstrap hygiene"
sections of the release's bash doctor. Every CLI derives its own
instructions from ONE canonical file (`instructions/AGENTS.md`); these
checks verify that each CLI's pointer still targets it, and that the
bootstrap itself stays within its size budgets and doesn't point to notes
that have vanished.
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from nexgen_core.i18n import t
from nexgen_core.paths import canonical_instructions
from nexgen_core.report import CheckOutcome, Severity

#: Size budget (bytes) for the canonical AGENTS.md bootstrap, beyond which
#: task-specific content should move into a load-on-demand note.
#: Overridable for installations with different conventions.
BOOTSTRAP_MAX_BYTES = int(os.environ.get("NEXGEN_BOOTSTRAP_MAX_BYTES", "40000"))

#: Size budget (bytes) for each detail note loaded on demand (the *.md
#: files under the Vault's 03-INFRA root).
NOTE_MAX_BYTES = int(os.environ.get("NEXGEN_NOTE_MAX_BYTES", "16000"))

_POINTER_RE = re.compile(r"`([^`]+\.md)`")


def check_canonical_instructions_present(vault_data: Path) -> CheckOutcome:
    """The canonical instructions file must exist: it is the single source
    every CLI derives its own instructions from."""
    canon = canonical_instructions(vault_data)
    if not canon.is_file():
        return CheckOutcome(
            id="instructions.canonical_present",
            severity=Severity.BROKEN,
            message=t("The canonical instructions file does not exist ({canon}).", canon=canon),
            action=t("Restore 03-INFRA/agent-universal-layer/instructions/AGENTS.md from the Vault."),
        )
    return CheckOutcome(
        id="instructions.canonical_present",
        severity=Severity.OK,
        message=t("Canonical instructions file AGENTS.md present"),
    )


def check_claude_pointer(vault_data: Path, home: Path) -> CheckOutcome:
    """~/CLAUDE.md must be a lightweight pointer to the canonical file, not
    a copy or a symlink: a copy silently diverges from the canonical."""
    canon = canonical_instructions(vault_data)
    claude_file = home / "CLAUDE.md"

    if claude_file.is_symlink() or not claude_file.is_file():
        return CheckOutcome(
            id="instructions.claude_pointer",
            severity=Severity.BROKEN,
            message=t(
                "~/CLAUDE.md must be a plain-text pointer file, not missing or a symlink ({claude_file}).",
                claude_file=claude_file,
            ),
            action=t("Write a short pointer referencing {canon} into {claude_file}.", canon=canon, claude_file=claude_file),
        )

    try:
        content = claude_file.read_text(encoding="utf-8")
    except OSError as exc:
        return CheckOutcome(
            id="instructions.claude_pointer",
            severity=Severity.UNDETERMINED,
            message=t("Could not read ~/CLAUDE.md: {error}", error=exc),
        )

    if str(canon) in content:
        return CheckOutcome(
            id="instructions.claude_pointer",
            severity=Severity.OK,
            message=t("~/CLAUDE.md correctly points to the canonical AGENTS.md file"),
        )
    return CheckOutcome(
        id="instructions.claude_pointer",
        severity=Severity.BROKEN,
        message=t("~/CLAUDE.md does not reference the canonical AGENTS.md file's path (risk of a duplicate copy silently diverging)."),
        action=t("Replace ~/CLAUDE.md with a pointer referencing {canon}.", canon=canon),
    )


def _pointer_outcome(cli_name: str, id_: str, pointer_file: Path, canon: Path, *, installed: bool) -> CheckOutcome | None:
    """A CLI->canonical pointer: OK if aligned, BROKEN if present but
    diverging or if the CLI is installed and the pointer is missing
    entirely. A CLI that was never installed is not a failure: it has no
    code path that will ever create this file, so it isn't reported at
    all."""
    if pointer_file.exists() or pointer_file.is_symlink():
        try:
            aligned = canon.is_file() and pointer_file.resolve() == canon.resolve()
        except OSError:
            aligned = False
        if not aligned and canon.is_file() and pointer_file.is_file() and not pointer_file.is_symlink():
            # Windows without symlink privileges: the guard leaves an identical copy
            # instead. Demanding a symlink here kept the doctor red on those machines
            # for good, and the remedy rewrote the same copy.
            aligned = pointer_file.read_bytes() == canon.read_bytes()
        if aligned:
            return CheckOutcome(id=id_, severity=Severity.OK, message=t("{cli_name} points to the canonical AGENTS.md file", cli_name=cli_name))
        return CheckOutcome(
            id=id_,
            severity=Severity.BROKEN,
            message=t(
                "{cli_name}'s instructions ({pointer_file}) do not point to the canonical AGENTS.md file.",
                cli_name=cli_name, pointer_file=pointer_file,
            ),
            action=t("Run 'agent-sync apply' to recreate the pointer."),
        )
    if installed:
        return CheckOutcome(
            id=id_,
            severity=Severity.BROKEN,
            message=t(
                "{cli_name} is installed but doesn't have the pointer to the canonical instructions yet ({pointer_file}).",
                cli_name=cli_name, pointer_file=pointer_file,
            ),
            action=t("Run 'agent-sync apply' to generate the pointer."),
        )
    return None


def check_cli_instruction_pointers(vault_data: Path, home: Path) -> list[CheckOutcome]:
    """Codex and Antigravity pointers to the canonical AGENTS.md file."""
    canon = canonical_instructions(vault_data)
    targets = (
        ("Codex", "instructions.codex_pointer", home / ".codex" / "AGENTS.md", "codex"),
        ("Antigravity", "instructions.antigravity_pointer", home / ".gemini" / "config" / "AGENTS.md", "agy"),
    )
    outcomes: list[CheckOutcome] = []
    for cli_name, id_, pointer_file, binary in targets:
        outcome = _pointer_outcome(cli_name, id_, pointer_file, canon, installed=shutil.which(binary) is not None)
        if outcome is not None:
            outcomes.append(outcome)
    return outcomes


def check_opencode_instructions(vault_data: Path, home: Path) -> CheckOutcome | None:
    """OpenCode V2's GLOBAL scope file must point at the canonical AGENTS.md.

    The V2 config schema accepts an `instructions` array but does not
    resolve its files (official docs, verified 2026-09-22), so checking
    that array -- what this check did until v2.2.0 -- was a false green.
    What the model actually receives is `~/.config/opencode/AGENTS.md`,
    which the guard symlinks at the canonical bootstrap.

    A real file (not a symlink) is the private identity layer's
    derivative: never "fixed" here, only reported. If it names the
    canonical path it is a pointer and counts as aligned; otherwise it is
    a warning, because replacing persona with a pointer is a human call,
    not a guard call. OpenCode never launched (no scope file, no config,
    no binary) is not a failure and isn't reported.
    """
    from nexgen_core.paths import canonical_instructions, opencode_agents_file

    canon = canonical_instructions(vault_data)
    scope = opencode_agents_file(home)
    installed = shutil.which("opencode") is not None or (home / ".opencode" / "bin" / "opencode").is_file()

    if scope.is_symlink():
        try:
            aligned = canon.is_file() and scope.resolve() == canon.resolve()
        except OSError:
            aligned = False
        if aligned:
            return CheckOutcome(
                id="instructions.opencode_pointer",
                severity=Severity.OK,
                message=t("OpenCode V2 loads the canonical AGENTS.md scope file"),
            )
        return CheckOutcome(
            id="instructions.opencode_pointer",
            severity=Severity.BROKEN,
            message=t("OpenCode's global AGENTS.md scope file points elsewhere ({scope}).", scope=scope),
            action=t("Run 'agent-sync apply' to restore it to canonical."),
        )
    if scope.is_file():
        try:
            content = scope.read_text(encoding="utf-8")
        except OSError as exc:
            return CheckOutcome(
                id="instructions.opencode_pointer",
                severity=Severity.UNDETERMINED,
                message=t("Could not read OpenCode's global AGENTS.md scope file: {error}", error=exc),
            )
        if str(canon) in content:
            return CheckOutcome(
                id="instructions.opencode_pointer",
                severity=Severity.OK,
                message=t("OpenCode's global AGENTS.md scope file references the canonical bootstrap"),
            )
        return CheckOutcome(
            id="instructions.opencode_pointer",
            severity=Severity.WARN,
            message=t(
                "OpenCode's global AGENTS.md is a real file, not the canonical pointer "
                "(private derivative?). V2 loads this file, not the 'instructions' array."
            ),
            action=t("Verify it references the canonical bootstrap, or restore the pointer with 'agent-sync apply'."),
        )
    if installed:
        return CheckOutcome(
            id="instructions.opencode_pointer",
            severity=Severity.BROKEN,
            message=t("OpenCode is installed but its global AGENTS.md scope file is missing ({scope}).", scope=scope),
            action=t("Run 'agent-sync apply' to generate it."),
        )
    return None


def _openclaw_workspace_states(
    owners: list[tuple[tuple[str, ...], Path]],
    block: str,
    roster: tuple[str, ...],
    leads: dict[str, str],
) -> tuple[list[str], list[str], tuple[Path, OSError] | None]:
    """What each workspace's `AGENTS.md` looks like: unaligned, absent folder, unreadable.

    Line endings are read as they are (`newline=""`), which is also how the guard
    writes them: comparing a CRLF file against a block built with LF would make
    every cycle rewrite a file that is already correct. Both managed blocks are
    checked: the instructions pointer and the org chart the roster decides.
    """
    from nexgen_core.openclaw.hierarchy import manual_language
    from nexgen_core.openclaw.org_block import (
        OPENCLAW_ORG_MARKERS,
        openclaw_file_state,
        openclaw_org_block,
    )

    unaligned: list[str] = []
    absent: list[str] = []
    for agents, workspace in owners:
        target = workspace / "AGENTS.md"
        if not workspace.is_dir():
            absent.append(str(workspace))
            continue
        try:
            text = ""
            if target.is_file():
                with target.open("r", encoding="utf-8", newline="") as handle:
                    text = handle.read()
        except OSError as exc:
            return unaligned, absent, (target, exc)
        states = [openclaw_file_state(text, block)]
        if agents:
            org = openclaw_org_block(agents, manual_language(text), roster, leads)
            states.append(
                openclaw_file_state(text, org, OPENCLAW_ORG_MARKERS, at_top=False)
            )
        state = next((value for value in states if value != "ok"), "ok")
        if state != "ok":
            unaligned.append(f"{target} ({state})")
    return unaligned, absent, None


def _openclaw_unaligned_outcome(unaligned: list[str], absent: list[str]) -> CheckOutcome:
    """The warning for the workspaces whose block is missing, different or half-written."""
    return CheckOutcome(
        id="instructions.openclaw_block",
        severity=Severity.WARN,
        message=t(
            "OpenClaw's AGENTS.md is not aligned in {count} workspace(s): {paths}.",
            count=len(unaligned), paths=", ".join(unaligned),
        ),
        action=t(
            "Run 'nexgen sync' to rewrite the managed block; the text outside it is yours and is never touched."
        ),
        detail=t("Workspace folders OpenClaw declares but don't exist yet: {paths}", paths=", ".join(absent))
        if absent else None,
    )


def check_openclaw_instructions(vault_data: Path, home: Path) -> CheckOutcome | None:
    """OpenClaw reads the `AGENTS.md` of each agent's workspace: both blocks must be there.

    There is no pointer file to check, unlike Codex and Antigravity: OpenClaw
    loads whatever `AGENTS.md` sits in the agent's working directory, truncated,
    so the engine owns two delimited blocks there and nothing else in it: the
    pointer to the canonical instructions, on top, and the org chart the roster
    decides, at the end. A workspace folder that does not exist yet is not a
    drift of the blocks: the setup command creates it.
    """
    from nexgen_core.openclaw.org_block import (
        openclaw_instruction_block,
        openclaw_squad_leads,
        openclaw_workspace_owners,
    )
    from nexgen_core.paths import openclaw_config

    config = openclaw_config(home)
    if not config.is_file():
        return None  # OpenClaw never set up here: nothing to align
    canon = canonical_instructions(vault_data)
    if not canon.is_file():
        return None  # already reported by instructions.canonical_present
    owners = openclaw_workspace_owners(config, home)
    roster = tuple(dict.fromkeys(agent for agents, _workspace in owners for agent in agents))
    leads = openclaw_squad_leads(vault_data, home)
    unaligned, absent, unreadable = _openclaw_workspace_states(
        owners, openclaw_instruction_block(canon), roster, leads,
    )
    if unreadable is not None:
        path, error = unreadable
        return CheckOutcome(
            id="instructions.openclaw_block",
            severity=Severity.UNDETERMINED,
            message=t("Could not read OpenClaw's instructions at {path}: {error}", path=path, error=error),
        )
    if unaligned:
        return _openclaw_unaligned_outcome(unaligned, absent)
    if absent:
        return CheckOutcome(
            id="instructions.openclaw_block",
            severity=Severity.WARN,
            message=t(
                "OpenClaw declares workspace folder(s) that don't exist yet: {paths}.",
                paths=", ".join(absent),
            ),
            action=t("Run 'nexgen openclaw setup' to create the agent workspace and its instructions."),
        )
    return CheckOutcome(
        id="instructions.openclaw_block",
        severity=Severity.OK,
        message=t(
            "OpenClaw reads the canonical AGENTS.md from {count} agent workspace(s)",
            count=len(owners),
        ),
    )


def check_bootstrap_size_budget(vault_data: Path) -> CheckOutcome | None:
    """The AGENTS.md bootstrap must stay within its own size budget. If the
    canonical file is missing, that's already reported elsewhere."""
    canon = canonical_instructions(vault_data)
    if not canon.is_file():
        return None

    size = canon.stat().st_size
    if size > BOOTSTRAP_MAX_BYTES:
        return CheckOutcome(
            id="instructions.bootstrap_size_budget",
            severity=Severity.BROKEN,
            message=t(
                "The AGENTS.md bootstrap is {size} bytes, over the {budget} budget.",
                size=size, budget=BOOTSTRAP_MAX_BYTES,
            ),
            action=t("Move task-specific content into a load-on-demand note, pulled in only when needed."),
        )
    return CheckOutcome(
        id="instructions.bootstrap_size_budget",
        severity=Severity.OK,
        message=t(
            "AGENTS.md bootstrap within budget ({size}/{budget} bytes)",
            size=size, budget=BOOTSTRAP_MAX_BYTES,
        ),
    )


def check_bootstrap_notes_size(vault_data: Path) -> CheckOutcome | None:
    """The detail notes loaded on demand (03-INFRA/*.md) must stay within
    their own size budget."""
    canon = canonical_instructions(vault_data)
    if not canon.is_file():
        return None

    notes_dir = vault_data / "03-INFRA"
    if not notes_dir.is_dir():
        return None

    oversized = [
        f"{note.name} ({note.stat().st_size}b)"
        for note in sorted(notes_dir.glob("*.md"))
        if note.stat().st_size > NOTE_MAX_BYTES
    ]
    if oversized:
        return CheckOutcome(
            id="instructions.bootstrap_notes_size",
            severity=Severity.WARN,
            message=t(
                "Detail note(s) over the {budget}-byte budget: {notes}.",
                budget=NOTE_MAX_BYTES, notes=", ".join(oversized),
            ),
            action=t("Consider splitting the note into smaller sections loaded on demand."),
        )
    return CheckOutcome(
        id="instructions.bootstrap_notes_size",
        severity=Severity.OK,
        message=t("Detail notes within the {budget}-byte budget", budget=NOTE_MAX_BYTES),
    )


def check_bootstrap_pointer_integrity(vault_data: Path) -> CheckOutcome | None:
    """Every load-on-demand pointer cited in backticks in the bootstrap
    (`03-INFRA/...md`, `99-INDEX/...md`, etc.) must resolve to a file that
    exists under the Vault. The valid roots are derived from the Vault's
    own filesystem, not a hardcoded list."""
    canon = canonical_instructions(vault_data)
    if not canon.is_file():
        return None

    try:
        content = canon.read_text(encoding="utf-8")
    except OSError as exc:
        return CheckOutcome(
            id="instructions.bootstrap_pointer_integrity",
            severity=Severity.UNDETERMINED,
            message=t("Could not read the AGENTS.md bootstrap: {error}", error=exc),
        )

    top_dirs = {p.name for p in vault_data.iterdir() if p.is_dir() and not p.name.startswith(".")}

    missing: list[str] = []
    checked = 0
    for ref in _POINTER_RE.findall(content):
        if "<" in ref or ">" in ref:
            continue
        first_segment = ref.split("/", 1)[0]
        if first_segment not in top_dirs:
            continue
        checked += 1
        if not (vault_data / ref).is_file():
            missing.append(ref)

    if checked == 0:
        return CheckOutcome(
            id="instructions.bootstrap_pointer_integrity",
            severity=Severity.OK,
            message=t("No load-on-demand pointers to check in the bootstrap"),
        )
    if missing:
        return CheckOutcome(
            id="instructions.bootstrap_pointer_integrity",
            severity=Severity.BROKEN,
            message=t(
                "Load-on-demand pointer(s) in the bootstrap that don't resolve to an existing file: {pointers}.",
                pointers=", ".join(missing),
            ),
            action=t("Fix or remove the references to renamed/removed notes in AGENTS.md."),
        )
    return CheckOutcome(
        id="instructions.bootstrap_pointer_integrity",
        severity=Severity.OK,
        message=t("All {checked} load-on-demand pointers in the bootstrap resolve correctly", checked=checked),
    )
