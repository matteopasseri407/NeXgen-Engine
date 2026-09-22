"""Which engine completed a cycle on this machine.

The transitional-launcher handover check used to live here; its subject
was deleted in v2.3.0 (the `.sh`/`.ps1` twins are gone and the release
preflight refuses their return), so a check for it would only be able to
report the absence of something that no longer exists. What remains is the
version record: answering it per machine is what turns "are all my
machines migrated?" from an impression into something readable.
"""
from __future__ import annotations

from pathlib import Path

from nexgen_core.i18n import t
from nexgen_core.report import CheckOutcome, Severity


def check_engine_version_recorded(state_dir: Path) -> CheckOutcome:
    """Which engine last completed a cycle on this machine.

    Answering it per machine is what turns "are all my machines migrated?"
    from an impression into something readable.
    """
    from nexgen_core import __version__
    from nexgen_core.beat import Heartbeat

    recorded = Heartbeat(state_dir=state_dir).recorded_version()
    if recorded is None:
        return CheckOutcome(
            id="takeover.version",
            severity=Severity.OK,
            message=t(
                "No engine version recorded here yet; this machine has not "
                "completed a cycle with a version that records one."
            ),
            action=t("It gets recorded on the first completed cycle."),
        )
    if recorded == __version__:
        return CheckOutcome(
            id="takeover.version",
            severity=Severity.OK,
            message=t("Last completed cycle: engine {version}", version=recorded),
        )
    return CheckOutcome(
        id="takeover.version",
        severity=Severity.OK,
        message=t(
            "The last completed cycle ran engine {recorded}, this one is "
            "{current}.",
            recorded=recorded,
            current=__version__,
        ),
        action=t("Normal right after an update; it lines up on the next cycle."),
    )
