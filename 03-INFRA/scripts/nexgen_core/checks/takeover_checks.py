"""Has this machine finished leaving the previous release behind?

Transitional compatibility only stops costing something when somebody
deletes it, and nobody deletes what they cannot see. These two checks make
the state visible per machine, so "can the legacy launchers go?" is answered
by looking rather than by remembering.

Neither is a fault, and both report OK: a machine mid-handover is working
exactly as intended. What they carry is the state, in the message — the
severity scale has three levels and none of them means "worth knowing", and
inventing a fourth would change a summary line the previous release parses.
"""
from __future__ import annotations

from pathlib import Path

from nexgen_core.i18n import t
from nexgen_core.report import CheckOutcome, Severity


def check_takeover_complete(home: Path | None = None) -> CheckOutcome:
    """Is anything on this machine still reaching the engine the old way?

    The previous release put symlinks in `~/.local/bin` pointing into the
    engine checkout at `<name>.sh`. The new engine replaces them with its own
    launchers the first time it runs. A symlink still pointing at a script
    means the handover has not happened here yet.
    """
    from nexgen_core.legacy_launchers import REMOVE_AFTER, takeover_complete

    done, pending = takeover_complete(home)
    if done:
        return CheckOutcome(
            id="takeover.launchers",
            severity=Severity.OK,
            message=t(
                "Handover complete: nothing here goes through the previous "
                "release's launchers any more."
            ),
        )
    return CheckOutcome(
        id="takeover.launchers",
        severity=Severity.OK,
        message=t(
            "{count} command(s) still reach the engine the previous release's "
            "way: {names}",
            count=len(pending),
            names=", ".join(pending),
        ),
        action=t(
            "Run 'nexgen sync apply' once: it replaces them. Until every "
            "machine reports this as complete, the transitional launchers "
            "cannot be removed (they are due after {version}).",
            version=REMOVE_AFTER,
        ),
    )


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
