"""Node attach progress monitoring and timeout recovery.

One job: monitor node connection progress, emit status every few seconds,
enforce a bounded deadline, and provide clear recovery steps on timeout.
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable

from nexgen_core.i18n import t
from nexgen_core.openclaw_service import RunResult

#: Default node attach wait timeout (5 minutes)
DEFAULT_NODE_ATTACH_TIMEOUT_S = 300.0
#: Report interval in seconds
DEFAULT_REPORT_INTERVAL_S = 5.0


def check_node_connected(
    runner: Callable[[list[str]], RunResult],
    cmd: list[str] | None = None,
) -> bool:
    """Queries local node status and returns whether node is connected to gateway."""
    run_cmd = cmd or ["openclaw", "node", "status", "--json"]
    try:
        res = runner(run_cmd)
        if res.returncode != 0 or not res.stdout.strip():
            return False
        data = json.loads(res.stdout)
        if isinstance(data, dict):
            status = str(data.get("status") or "").strip().lower()
            return bool(data.get("connected")) or status == "connected"
    except Exception:  # nexgen: allow reason="node status query error is safely treated as not connected"
        return False
    return False


def wait_for_node_connection(
    probe_fn: Callable[[], bool],
    node_name: str = "node",
    timeout_s: float = DEFAULT_NODE_ATTACH_TIMEOUT_S,
    report_interval_s: float = DEFAULT_REPORT_INTERVAL_S,
    sleeper: Callable[[float], None] = time.sleep,
    time_provider: Callable[[], float] = time.monotonic,
    emit: Callable[[str], None] = print,
) -> tuple[bool, float]:
    """Waits for node to connect to gateway, printing a line every few seconds."""
    started = time_provider()
    deadline = started + max(0.0, timeout_s)
    last_reported = -report_interval_s

    while True:
        if probe_fn():
            elapsed = time_provider() - started
            emit(t("Node '{name}' connected to gateway ({seconds}s).",
                   name=node_name, seconds=int(elapsed)))
            return True, elapsed

        elapsed = time_provider() - started
        if elapsed >= timeout_s:
            emit(t("Node '{name}' did not connect within {timeout}s.",
                   name=node_name, timeout=int(timeout_s)))
            emit(t("Recovery steps: check gateway reachability (e.g. Tailscale), "
                   "inspect node logs with 'openclaw node logs', "
                   "or restart with 'openclaw node restart'."))
            return False, elapsed

        if elapsed - last_reported >= report_interval_s:
            last_reported = elapsed
            emit(t("Waiting for node '{name}' to connect to gateway: {seconds}s elapsed...",
                   name=node_name, seconds=int(elapsed)))

        sleeper(min(1.0, max(0.1, deadline - time_provider())))
