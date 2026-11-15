"""The three prompt-size limits the engine writes, and why exactly these numbers.

One job: propose `agents.defaults.bootstrapMaxChars`,
`agents.defaults.bootstrapTotalMaxChars` and `skills.limits.maxSkillsPromptChars`
at the values OpenClaw documents, and say in the doctor when the file in force
holds a value that would cut something the engine put there.

Why write a default at all: OpenClaw is free to change what its own default is,
and a change like that moves the cost of every session without touching the
engine. Writing the documented number makes the limit a decision in the file a
person can read and change, which is what the rest of this configuration already
is. It is never written over a value the file already holds: that number is
somebody's decision, and it stays.

Where the numbers come from, read on 2026-11-15:

- `agents.defaults.bootstrapMaxChars`, default `20000`, and
  `agents.defaults.bootstrapTotalMaxChars`, default `60000`: the limits
  `docs.openclaw.ai/reference/token-use` names for the injected bootstrap files,
  and the constants `DEFAULT_BOOTSTRAP_MAX_CHARS = 2e4` and
  `DEFAULT_BOOTSTRAP_TOTAL_MAX_CHARS = 6e4` in the pinned release's own bundle.
  The per-file limit is what would truncate a workspace `AGENTS.md`:
  `org_block` therefore writes its pointer at the top of that file, and the
  doctor below refuses a value shorter than the pointer itself.
- `skills.limits.maxSkillsPromptChars`, default `18000`: the budget
  `docs.openclaw.ai/tools/skills` describes for the rendered skills block (and
  the per-agent override `agents.entries.*.skillsLimits.maxSkillsPromptChars`),
  whose default is `DEFAULT_MAX_SKILLS_PROMPT_CHARS = 18e3` in the same bundle.

Both paths were accepted by OpenClaw 2026.11.2 itself:
`openclaw config patch --file <patch> --dry-run --json` answered
`{"ok": true, "operations": 3, "checks": {"schema": true}}` for exactly these
three keys.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from nexgen_core.i18n import t
from nexgen_core.openclaw.org_block import openclaw_instruction_block
from nexgen_core.report import CheckOutcome, Severity

#: Characters of one injected bootstrap file, and of all of them together.
DOCUMENTED_BOOTSTRAP_MAX_CHARS = 20_000
DOCUMENTED_BOOTSTRAP_TOTAL_MAX_CHARS = 60_000

#: Characters of the rendered skills block in a session's system prompt.
DOCUMENTED_MAX_SKILLS_PROMPT_CHARS = 18_000

CHECK_ID = "openclaw.prompt_budget"


def apply(config: dict[str, Any], current: dict[str, Any] | None) -> dict[str, Any]:
    """Adds the three limits to a configuration being built, where the file does not decide.

    One call because the three numbers are one decision: what a session may spend
    on instructions, read from two places OpenClaw keeps apart. Additive both
    times: `agents.defaults` is merged key by key, and `skills` may already carry
    the per-skill `entries` that `skills_policy` writes beside `limits`.
    """
    agents = config.get("agents") if isinstance(config.get("agents"), dict) else None
    if agents is not None:
        defaults = agents.get("defaults") if isinstance(agents.get("defaults"), dict) else {}
        agents["defaults"] = {**defaults, **bootstrap_defaults(current)}
    limits = skills_limits(current)
    if limits:
        skills = config.get("skills") if isinstance(config.get("skills"), dict) else {}
        declared = skills.get("limits") if isinstance(skills.get("limits"), dict) else {}
        config["skills"] = {**skills, "limits": {**declared, **limits}}
    return config


def bootstrap_defaults(current: dict[str, Any] | None) -> dict[str, int]:
    """The two `agents.defaults` limits to propose, or nothing where the file decides.

    `org_block` puts its pointer at the top of a workspace `AGENTS.md`, so the
    per-file limit is the one that could cut it; the documented `20000` is more
    than forty times the block's own size (446 characters on a desktop path), and
    a machine that set something smaller has made a decision this module does not
    overrule.
    """
    defaults = _agent_defaults(current)
    proposed: dict[str, int] = {}
    for key, documented in (
        ("bootstrapMaxChars", DOCUMENTED_BOOTSTRAP_MAX_CHARS),
        ("bootstrapTotalMaxChars", DOCUMENTED_BOOTSTRAP_TOTAL_MAX_CHARS),
    ):
        if _positive(defaults.get(key)) is None:
            proposed[key] = documented
    return proposed


def skills_limits(current: dict[str, Any] | None) -> dict[str, int]:
    """The `skills.limits` limits to propose, or nothing where the file decides."""
    if _positive(_limits_block(current).get("maxSkillsPromptChars")) is not None:
        return {}
    return {"maxSkillsPromptChars": DOCUMENTED_MAX_SKILLS_PROMPT_CHARS}


def check(config: dict[str, Any], canon: Path) -> CheckOutcome:
    """The limits in force, and whether the per-file one can still hold the pointer.

    `canon` is the canonical instructions file a workspace `AGENTS.md` points at:
    the pointer block's text depends on nothing else, so the length it would need
    in the injected file is read here without touching the Vault.
    """
    defaults = _agent_defaults(config)
    per_file = _positive(defaults.get("bootstrapMaxChars"))
    total = _positive(defaults.get("bootstrapTotalMaxChars"))
    skills_chars = _positive(_limits_block(config).get("maxSkillsPromptChars"))
    pointer = len(openclaw_instruction_block(canon))

    if per_file is not None and per_file < pointer:
        return CheckOutcome(
            id=CHECK_ID,
            severity=Severity.WARN,
            message=t("agents.defaults.bootstrapMaxChars is {limit} characters, and the pointer block the "
                      "engine puts at the top of a workspace AGENTS.md alone needs {needed}: OpenClaw truncates "
                      "the injected file, so the pointer to the canonical rules is cut.",
                      limit=per_file, needed=pointer),
            action=t("Raise it to at least {needed} characters, or remove the key to take the documented "
                     "{documented}.", needed=pointer, documented=DOCUMENTED_BOOTSTRAP_MAX_CHARS),
        )
    decided = sum(1 for value in (per_file, total, skills_chars) if value is not None)
    return CheckOutcome(
        id=CHECK_ID,
        severity=Severity.OK,
        message=t("Prompt size limits: {per_file} characters per injected file, {total} in total, {skills} for "
                  "the skills catalog ({source}).",
                  per_file=per_file or DOCUMENTED_BOOTSTRAP_MAX_CHARS,
                  total=total or DOCUMENTED_BOOTSTRAP_TOTAL_MAX_CHARS,
                  skills=skills_chars or DOCUMENTED_MAX_SKILLS_PROMPT_CHARS,
                  source=(
                      t("documented defaults") if decided == 0
                      else t("as this file sets them") if decided == 3
                      else t("{count} set by this file, the rest by the documented defaults", count=decided)
                  )),
    )


def _agent_defaults(current: dict[str, Any] | None) -> dict[str, Any]:
    """`agents.defaults` as a mapping, or nothing when the file has none."""
    agents = current.get("agents") if isinstance(current, dict) else None
    defaults = agents.get("defaults") if isinstance(agents, dict) else None
    return defaults if isinstance(defaults, dict) else {}


def _limits_block(current: dict[str, Any] | None) -> dict[str, Any]:
    """`skills.limits` as a mapping, or nothing when the file has none."""
    skills = current.get("skills") if isinstance(current, dict) else None
    limits = skills.get("limits") if isinstance(skills, dict) else None
    return limits if isinstance(limits, dict) else {}


def _positive(value: Any) -> int | None:
    """A size limit somebody set, or None when the file does not decide one."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None
