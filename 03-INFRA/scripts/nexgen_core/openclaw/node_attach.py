"""Node attach progress monitoring, connection, and timeout recovery.

One job: connect node via --target-file without process arg exposure, monitor
connection progress, enforce a bounded deadline, and provide clear recovery steps
citing only documented OpenClaw commands.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from nexgen_core.files import atomic_write_text
from nexgen_core.i18n import t
from nexgen_core.openclaw.node_cli import READ_TIMEOUT_S, SERVICE_TIMEOUT_S, Run, run_command

#: Default node attach wait timeout (300 seconds / 5 minutes)
DEFAULT_NODE_ATTACH_TIMEOUT_S = 300.0
#: Report interval in seconds
DEFAULT_REPORT_INTERVAL_S = 3.0


def connect_node_service(
    target: str,
    home: Path | None = None,
    runner: Run | None = None,
    out: Callable[[str], None] = print,
) -> bool:
    """Connects node as an OS service using --target-file to protect secrets."""
    run_fn = runner or run_command
    temp_dir = Path(tempfile.gettempdir())
    target_file = temp_dir / f"openclaw-connect-{os.getpid()}-{int(time.time())}.tmp"

    try:
        atomic_write_text(target_file, target.strip(), preserve_mode=False)

        out(t("Connecting node to gateway via 'openclaw connect --service --session-host' (protected target file)..."))
        cmd = ["openclaw", "connect", "--service", "--session-host", "--target-file", str(target_file)]
        res = run_fn(cmd, SERVICE_TIMEOUT_S)
        if res.returncode != 0:
            out(t("Node connect failed (exit code {code}).", code=res.returncode))
            return False
        out(t("Node connect command completed."))
        return True
    finally:
        target_file.unlink(missing_ok=True)


def check_node_connected(
    runner: Run | None = None,
    cmd: list[str] | None = None,
) -> bool:
    """Queries node status via documented 'openclaw node status --json'."""
    run_fn = runner or run_command
    run_cmd = cmd or ["openclaw", "node", "status", "--json"]
    try:
        res = run_fn(run_cmd, READ_TIMEOUT_S)
        if res.returncode != 0 or not res.stdout.strip():
            return False
        try:
            data = json.loads(res.stdout)
            from nexgen_core.openclaw.node_status import parse_node_status_payload

            parsed = parse_node_status_payload(data)
            return parsed.is_running
        except json.JSONDecodeError:
            out_lower = res.stdout.lower()
            return ("running" in out_lower or "connected" in out_lower) and "stopped" not in out_lower
    except Exception:  # nexgen: allow reason="node status query error is safely treated as not connected"
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
    """Waits for node to connect to gateway, printing progress every few seconds."""
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
            emit(t("Recovery steps (only documented commands):"))
            emit(t("  - Inspect node status: 'openclaw node status'"))
            emit(t("  - Restart node service: 'openclaw node restart'"))
            emit(t("  - Test in foreground: 'openclaw node run --session-host'"))
            emit(t("  - On the gateway host, mint a fresh join code: 'openclaw devices join-code'"))
            return False, elapsed

        if elapsed - last_reported >= report_interval_s:
            last_reported = elapsed
            emit(t("Waiting for node '{name}' to connect to gateway: {seconds}s elapsed...",
                   name=node_name, seconds=int(elapsed)))

        sleeper(min(1.0, max(0.1, deadline - time_provider())))
