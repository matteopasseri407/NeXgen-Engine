"""Whether this machine is quiet enough for an automatic engine update.

One job: answer "is the machine free right now?" from the signals the engine
already keeps, so an automatic update onto a moving channel never swaps the engine out while a
person (or a guard cycle) is working on it. Postponing is the answer, not
failing: the next cycle retries by itself, and an alert for "the person was
working" would only train people to ignore alerts.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from nexgen_core.lock import LockIOError, LockTimeoutError, host_mutation

logger = logging.getLogger("nexgen.update")

#: How long after a guard cycle an automatic moving-channel update counts the machine as busy.
BUSY_WINDOW_SECONDS = 300.0


def machine_is_busy(state_dir: Path) -> str | None:
    """Why an automatic update must wait, or None when the machine looks quiet.

    Two signals the engine already keeps: the host lock, which every path that
    changes this machine takes (so a `nexgen` command a person is running holds
    it), and the guard's liveness file, written at the end of a cycle.
    """
    probe = host_mutation("nexgen-update-defer-probe", state_dir=state_dir, timeout=0)
    try:
        probe.acquire()
    except LockTimeoutError:
        return "another nexgen command is running"
    except (LockIOError, OSError) as exc:
        return f"the host lock cannot be inspected ({type(exc).__name__})"
    finally:
        probe.release()

    from nexgen_core.beat import LIVENESS_FILE_NAME  # lazy: beat imports the updater

    try:
        age = time.time() - (state_dir / LIVENESS_FILE_NAME).stat().st_mtime
    except OSError:  # nexgen: allow reason="no liveness file means no recent guard cycle: the machine is quiet"
        return None
    return "a guard cycle just ran and may still be working" if age < BUSY_WINDOW_SECONDS else None


def defer_if_busy(*, unattended: bool, channel: str, state_dir: Path) -> bool:
    """True when an automatic update onto a moving branch must wait for a quiet machine.

    A release keeps its existing behaviour, busy lock and all: the ceiling and
    the accepted-release memory already cap it. nightly and beta are the case this
    rule exists for, because it installs a branch that moves during the day.
    """
    from nexgen_core.update_channel import DEFAULT_CHANNEL

    if not (unattended and channel != DEFAULT_CHANNEL):
        return False
    reason = machine_is_busy(state_dir)
    if not reason:
        return False
    logger.info("unattended %s update postponed: %s", channel, reason)
    print(f"Postponed to the next cycle: {reason}. Nothing was changed.")
    return True
