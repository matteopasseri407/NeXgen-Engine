"""Checks for the canonical instructions and AGENTS.md bootstrap hygiene.

Ported from the "Canonical instructions" and "Canonical bootstrap hygiene"
sections of the release's bash doctor. Every CLI derives its own
instructions from ONE canonical file (`instructions/AGENTS.md`); these
checks verify that each CLI's pointer still targets it, and that the
bootstrap itself stays within its size budgets and doesn't point to notes
that have vanished.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

from nexgen_core.i18n import t
from nexgen_core.jsonc import parse_jsonc
from nexgen_core.paths import canonical_instructions
from nexgen_core.renderer import McpRenderer
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
    """OpenCode's config `instructions` array must include the canonical
    AGENTS.md file. OpenCode never launched (no config) is not a failure
    and isn't reported."""
    renderer = McpRenderer(vault_data=vault_data, home=home)
    cfg_file = renderer._opencode_config_path()
    installed = shutil.which("opencode") is not None or (home / ".opencode" / "bin" / "opencode").is_file()

    if not cfg_file.is_file():
        if installed:
            return CheckOutcome(
                id="instructions.opencode_pointer",
                severity=Severity.BROKEN,
                message=t("OpenCode is installed but its configuration file is missing ({cfg_file}).", cfg_file=cfg_file),
                action=t("Run 'agent-sync apply' to generate it."),
            )
        return None

    try:
        raw = cfg_file.read_text(encoding="utf-8")
        data = parse_jsonc(raw) if cfg_file.suffix == ".jsonc" else json.loads(raw)
    except (OSError, ValueError) as exc:
        return CheckOutcome(
            id="instructions.opencode_pointer",
            severity=Severity.UNDETERMINED,
            message=t("Could not parse the OpenCode configuration ({cfg_file}): {error}", cfg_file=cfg_file, error=exc),
        )

    entries = data.get("instructions", []) if isinstance(data, dict) else []
    canon_suffix = "/agent-universal-layer/instructions/agents.md"
    matches = sum(
        1
        for item in entries
        if isinstance(item, str) and item.replace("\\", "/").rstrip("/").lower().endswith(canon_suffix)
    )

    if matches == 0:
        return CheckOutcome(
            id="instructions.opencode_pointer",
            severity=Severity.BROKEN,
            message=t("OpenCode's 'instructions' do not include the canonical AGENTS.md file."),
            action=t("Run 'agent-sync apply' to register it."),
        )
    return CheckOutcome(
        id="instructions.opencode_pointer",
        severity=Severity.OK,
        message=t("OpenCode loads the canonical AGENTS.md file"),
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
            severity=Severity.BROKEN,
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
