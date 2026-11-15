"""The two judges of a parity record: the doctor's line, and the release preflight.

One job: read the record `parity.py` leaves, say what a person needs to know about
it in one doctor line, and answer the one question a release preflight asks -- may
this release be tagged on this evidence. Neither function measures anything: each
lane costs two model calls, so a doctor on a schedule that measured would bill a
person every hour, and a preflight runs where the gateway is not.

The rule behind both is the owner's: an agent that runs a harness inside OpenClaw
must cost per call what the same harness costs run directly, plus a light overhead,
and the tolerance is `PARITY_OVERHEAD_TOKENS`. A record that fails it, is older than
`RECORD_MAX_AGE_DAYS`, or was measured on another OpenClaw version or before the
commit being released does not speak for this release.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from nexgen_core.i18n import t
from nexgen_core.openclaw import parity_probe as probe
from nexgen_core.openclaw.parity_record import (
    PARITY_OVERHEAD_TOKENS,
    RECORD_MAX_AGE_DAYS,
    LaneResult,
    Record,
    load,
    record_path,
    state_record_path,
)
from nexgen_core.report import CheckOutcome, Severity

#: The doctor line this module adds. The id is a member of the `openclaw.parity.*`
#: family, which already answers whether two hosts receive the same configuration,
#: because this one answers a different question: what the same call costs on each side.
CHECK_ID = "openclaw.parity.tokens"


def check(
    *,
    vault: Path,
    openclaw_version: str = "",
    home: Path | None = None,
    now: float | None = None,
) -> CheckOutcome:
    """The doctor's one line: how old the record is, and the worst overhead it measured.

    It reads what the last run kept, the machine-local copy as well as the Vault's,
    so a gateway that has just measured sees its own record before the next pull.
    A failure outranks staleness: a stale record asks for a new measurement, a
    failing one says the machine is over the line the owner drew.
    """
    record = load((record_path(vault), state_record_path(home)))
    if record is None:
        return CheckOutcome(
            id=CHECK_ID,
            severity=Severity.WARN,
            message=t("No OpenClaw token parity record this machine can read: nothing proves a harness inside "
                      "OpenClaw costs what it costs run directly."),
            action=t("Run 'nexgen openclaw parity' on the gateway: it measures each lane and keeps the record."),
        )
    failed = record.failed
    if failed:
        return CheckOutcome(
            id=CHECK_ID,
            severity=Severity.BROKEN,
            message=t("OpenClaw's overhead is over the {limit:,} input tokens the owner allows on {lanes}.",
                      limit=PARITY_OVERHEAD_TOKENS, lanes=lanes_line(failed)),
            action=t("Run 'nexgen openclaw parity' on the gateway after fixing the lane: no release ships "
                     "on this record."),
        )
    age = record.age_days(now)
    stale = _stale_sentence(age)
    if stale:
        return CheckOutcome(
            id=CHECK_ID,
            severity=Severity.WARN,
            message=stale,
            action=t("Run 'nexgen openclaw parity' on the gateway: the record has to describe the release "
                     "that is shipping."),
        )
    installed = probe.version_of(openclaw_version)
    if installed and record.openclaw_version and installed != record.openclaw_version:
        return CheckOutcome(
            id=CHECK_ID,
            severity=Severity.WARN,
            message=t("The OpenClaw token parity record was measured on OpenClaw {recorded}, and this machine "
                      "runs {installed}.", recorded=record.openclaw_version, installed=installed),
            action=t("Run 'nexgen openclaw parity' on the gateway: a new OpenClaw is a new prompt to measure."),
        )
    return CheckOutcome(
        id=CHECK_ID,
        severity=Severity.OK,
        message=t("OpenClaw token parity {age}: {worst}.", age=_age_sentence(age), worst=_worst_sentence(record)),
    )


def release_refusal(
    *, vault: Path, is_ancestor: Callable[[str], bool], now: float | None = None,
) -> str | None:
    """Why this release may not be tagged, or None when the Vault record covers it.

    One plain English sentence, because that is the language the release preflight
    prints: what is wrong, and the one command that produces a good record.
    """
    target = record_path(vault)
    record = load((target,))
    if record is None:
        return (f"there is no OpenClaw parity record at {target}, and a release may not ship until a harness "
                f"inside OpenClaw has been measured: run 'nexgen openclaw parity' on the gateway and publish "
                f"the record")
    failed = record.failed
    if failed:
        return (f"the OpenClaw parity record failed on {lanes_line(failed)}, and a release may not ship on it: "
                f"run 'nexgen openclaw parity' on the gateway and publish a record that passes")
    if not record.measured:
        return ("the OpenClaw parity record measured no lane on both sides, so it proves nothing about what a "
                "harness costs inside OpenClaw: run 'nexgen openclaw parity' on the gateway, where the agents run")
    age = record.age_days(now)
    if age is None or age > RECORD_MAX_AGE_DAYS:
        measured = "carries no date" if age is None else f"is {int(age)} days old"
        return (f"the OpenClaw parity record {measured}, and {RECORD_MAX_AGE_DAYS} days is the limit: "
                f"re-run 'nexgen openclaw parity' on the gateway")
    if not record.engine_commit:
        return ("the OpenClaw parity record names no engine commit, so it cannot be tied to this release: "
                "re-run 'nexgen openclaw parity' on the gateway")
    if not is_ancestor(record.engine_commit):
        return (f"the OpenClaw parity record was measured on engine commit {record.engine_commit}, which is not "
                f"part of this release: re-run 'nexgen openclaw parity' on the gateway from this commit")
    return None


def lanes_line(failed: tuple[LaneResult, ...]) -> str:
    """The lanes as one line, each with the number that failed it."""
    parts: list[str] = []
    for result in failed:
        overhead = result.overhead
        parts.append(f"{result.lane} ({overhead:+,})" if overhead is not None else result.lane)
    return ", ".join(parts)


def _stale_sentence(age: float | None) -> str:
    """Why a record is too old to speak for a release, or "" when it is not."""
    if age is None:
        return t("The OpenClaw token parity record carries no date this machine can read.")
    if age > RECORD_MAX_AGE_DAYS:
        return t("The OpenClaw token parity record is {days} days old, and {allowed} is the limit.",
                 days=int(age), allowed=RECORD_MAX_AGE_DAYS)
    return ""


def _age_sentence(age: float | None) -> str:
    """How old a record is, in the words a person reads."""
    if age is None:
        return t("is undated")
    if int(age) == 0:
        return t("was measured less than a day ago")
    if int(age) == 1:
        return t("was measured a day ago")
    return t("was measured {days} days ago", days=int(age))


def _worst_sentence(record: Record) -> str:
    """The worst measured lane, or the fact that no lane had two numbers."""
    worst = record.worst
    if worst is None or worst.overhead is None:
        return t("no lane was measurable")
    return t("the worst lane adds {overhead:,} input tokens ({lane}), under the {limit:,} allowed",
             overhead=worst.overhead, lane=worst.lane, limit=PARITY_OVERHEAD_TOKENS)
