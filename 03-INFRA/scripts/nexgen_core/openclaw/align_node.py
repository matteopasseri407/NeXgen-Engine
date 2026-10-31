"""One job: restart the node service after an OpenClaw upgrade and prove it came back.

Why it exists: the first start of a new OpenClaw release can migrate its state
database, and for those seconds every CLI call that touches the state fails with a
generic "The CLI command failed." On a Windows node (2026-10-31, 2026.10.6 to
2026.10.7) one such answer right after the restart rolled the upgrade back and took
the whole engine update down with it, although the same commands passed a minute
later. So the restart and the status check get a short, bounded number of attempts:
a node that is still starting is waited for, a node that never comes back is still
reported and rolled back by `version_align.py`.
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping

from nexgen_core.i18n import t
from nexgen_core.openclaw.node_status import parse_node_status_payload
from nexgen_core.openclaw_service import RunResult

#: How many times a restart or a status check is asked, and the pause between two asks.
ATTEMPTS = 10
PAUSE_SECONDS = 3.0

Run = Callable[..., RunResult]


def restart(run: Run, binary: str, env: Mapping[str, str]) -> str | None:
    """`openclaw node restart`, asked again while it fails; the last reason, or None."""
    problem: str | None = None
    for attempt in range(ATTEMPTS):
        if attempt:
            _pause(PAUSE_SECONDS)
        result = run([binary, "node", "restart"], env)
        if result.returncode == 0:
            return None
        problem = tail(result)
    return problem


def running(run: Run, binary: str, env: Mapping[str, str]) -> str | None:
    """The node service reports running within the attempts; or why it never did."""
    problem: str | None = None
    for attempt in range(ATTEMPTS):
        if attempt:
            _pause(PAUSE_SECONDS)
        problem = _status_problem(run([binary, "node", "status", "--json"], env))
        if problem is None:
            return None
    return problem


def _status_problem(result: RunResult) -> str | None:
    """One `openclaw node status --json` answer, judged: None when the node runs."""
    if result.returncode != 0:
        return tail(result)
    try:
        status = parse_node_status_payload(json.loads(result.stdout))
    except ValueError:  # nexgen: allow reason="an older CLI prints text instead of JSON, and the text is judged below"
        text = f"{result.stdout}\n{result.stderr}".casefold()
        return None if "running" in text else t("the node service is not running")
    return None if status.is_running else t("the node service is not running")


def tail(result: RunResult) -> str:
    """The last line a failed program printed: the reason, not the whole log."""
    lines = [line.strip() for line in f"{result.stdout or ''}\n{result.stderr or ''}".splitlines() if line.strip()]
    return lines[-1] if lines else t("no output")


def _pause(seconds: float) -> None:
    """The wait between two attempts, in one place so a test skips it instead of sleeping."""
    time.sleep(seconds)
