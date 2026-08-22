"""The boundary between runtime presentation and private identity.

Two different things, and confusing them has already cost six days of
silence:

- a **boundary violation** is a structured memory living alongside the
  Vault. The Vault is the memory; a second memory means two truths that
  diverge, and it must be reported as a failure.
- a **shape defect** is a missing or malformed metadata field in the
  personal space. It must be reported, but it cannot carry the same weight
  as the first: on August 14th a missing frontmatter line made the identity
  guard exit with an error, systemd tore down the downstream sync, and for
  six days nobody noticed because the megaphone lived inside the dead
  service.

The tool that *repairs* this boundary lives in the private data and is
outside the public engine's perimeter. Here there is only judgment, which is
the doctor's job.
"""
from __future__ import annotations

import re
from pathlib import Path

from nexgen_core.i18n import t
from nexgen_core.report import CheckOutcome, Severity

#: Where each runtime keeps its own *structured* memory. Session
#: transcripts are deliberately not in this list: they are raw material to
#: be distilled, not a second truth.
NATIVE_MEMORY_STORES: dict[str, tuple[str, ...]] = {
    "claude": (".claude/memory",),
}

#: The minimum frontmatter that makes the personal space readable.
REQUIRED_FRONTMATTER = ("status",)

#: Valid lifecycle states. Anything else is a shape defect: reported, not
#: blocking, exactly like a missing key.
VALID_STATUSES = ("uninitialized", "active")

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _self_file(vault_data: Path) -> Path:
    return vault_data / "03-INFRA" / "agent-universal-layer" / "agent-self.md"


def check_agent_self(vault_data: Path) -> CheckOutcome:
    """Does the personal space exist and is it readable?

    Missing is not a failure: nobody is required to have one. Present but
    unreadable is, because something broke it without the user choosing to.
    """
    self_file = _self_file(vault_data)
    if not self_file.is_file():
        return CheckOutcome(
            id="identity.self_present",
            severity=Severity.UNDETERMINED,
            message=t("The assistant's personal space does not exist on this machine yet."),
            action=t("No action needed, unless you want to create it."),
        )
    try:
        self_file.read_text(encoding="utf-8")
    except OSError as exc:
        return CheckOutcome(
            id="identity.self_present",
            severity=Severity.BROKEN,
            message=t("The assistant's personal space exists but cannot be read."),
            action=t("Check the permissions on {self_file}", self_file=self_file),
            detail=str(exc),
        )
    return CheckOutcome(
        id="identity.self_present",
        severity=Severity.OK,
        message=t("Personal space present and readable"),
    )


def check_agent_self_metadata(vault_data: Path) -> CheckOutcome:
    """Is the personal space's frontmatter in order?

    Shape defect: it gets reported, it stops nothing. This is exactly the
    case that took down the whole layer on August 14th over one missing
    line.
    """
    self_file = _self_file(vault_data)
    if not self_file.is_file():
        return CheckOutcome(
            id="identity.self_metadata",
            severity=Severity.OK,
            message=t("No frontmatter to check"),
        )
    try:
        content = self_file.read_text(encoding="utf-8")
    except OSError:
        return CheckOutcome(
            id="identity.self_metadata",
            severity=Severity.UNDETERMINED,
            message=t("Could not read the personal space to check its metadata."),
        )

    match = _FRONTMATTER_RE.match(content)
    if match is None:
        return CheckOutcome(
            id="identity.self_metadata",
            severity=Severity.BROKEN,
            message=t("The personal space has no metadata header."),
            action=t("Add a '---' block with 'status:' at the top of {filename}", filename=self_file.name),
            detail="Shape defect: it does not stop anything from working.",
        )

    body = match.group(1)
    missing = [key for key in REQUIRED_FRONTMATTER if not re.search(rf"^\s*{key}\s*:", body, re.MULTILINE)]
    if missing:
        return CheckOutcome(
            id="identity.self_metadata",
            severity=Severity.BROKEN,
            message=t("The personal space is missing {fields} in its metadata.", fields=", ".join(missing)),
            action=t("Add '{field}:' to the header of {filename}", field=missing[0], filename=self_file.name),
            detail="Shape defect: it does not stop anything from working.",
        )
    status_match = re.search(r"^\s*status\s*:\s*(\S+)", body, re.MULTILINE)
    if status_match and status_match.group(1).strip().lower() not in VALID_STATUSES:
        return CheckOutcome(
            id="identity.self_metadata",
            severity=Severity.WARN,
            message=t(
                "The personal space declares status '{status}', which is not one of {valid}.",
                status=status_match.group(1), valid=", ".join(VALID_STATUSES),
            ),
            action=t("Use '{valid}' in the header of {filename}", valid=" / ".join(VALID_STATUSES), filename=self_file.name),
            detail="Shape defect: it does not stop anything from working.",
        )
    return CheckOutcome(
        id="identity.self_metadata",
        severity=Severity.OK,
        message=t("Personal space metadata is in order"),
    )


def check_native_memory_boundary(home: Path) -> CheckOutcome:
    """Does a structured memory exist alongside the Vault?

    Boundary violation: two memories are two truths, and sooner or later
    they diverge. Nothing gets deleted — it just gets reported.
    """
    found: list[str] = []
    for runtime, locations in NATIVE_MEMORY_STORES.items():
        for relative in locations:
            store = home / relative
            if not store.is_dir():
                continue
            populated = any(p.is_file() for p in store.rglob("*"))
            if populated:
                found.append(f"{runtime} ({store})")

    if not found:
        return CheckOutcome(
            id="identity.native_memory_boundary",
            severity=Severity.OK,
            message=t("No native memory alongside the Vault"),
        )

    found_list = ", ".join(found)
    if len(found) == 1:
        message = t(
            "A runtime keeps a memory of its own alongside the Vault: {found}. "
            "Two separate memories end up saying different things.",
            found=found_list,
        )
    else:
        message = t(
            "Some runtimes keep a memory of their own alongside the Vault: {found}. "
            "Two separate memories end up saying different things.",
            found=found_list,
        )

    return CheckOutcome(
        id="identity.native_memory_boundary",
        severity=Severity.BROKEN,
        message=message,
        action=t("nexgen inventory   # to see what it holds before deciding"),
        detail="Nothing gets deleted: the decision is yours.",
    )
