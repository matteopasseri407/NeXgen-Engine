"""Node attach progress monitoring, connection, and timeout recovery.

One job: connect node via --target-file without process arg exposure, monitor
connection progress, enforce a bounded deadline, and provide clear recovery steps
citing only documented OpenClaw commands.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from nexgen_core.i18n import t
from nexgen_core.openclaw_service import RunResult

#: Default node attach wait timeout (60 seconds)
DEFAULT_NODE_ATTACH_TIMEOUT_S = 60.0
#: Report interval in seconds
DEFAULT_REPORT_INTERVAL_S = 3.0


def _default_runner(cmd: list[str]) -> RunResult:
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return RunResult(res.returncode, res.stdout, res.stderr)


def connect_node_service(
    target: str,
    home: Path | None = None,
    runner: Callable[[list[str]], RunResult] | None = None,
    out: Callable[[str], None] = print,
) -> bool:
    """Connects node as an OS service using --target-file to protect secrets."""
    run_fn = runner or _default_runner
    temp_dir = Path(tempfile.gettempdir())
    target_file = temp_dir / f"openclaw-connect-{os.getpid()}-{int(time.time())}.tmp"

    try:
        target_file.write_text(target.strip(), encoding="utf-8")
        if sys.platform != "win32":
            try:
                target_file.chmod(0o600)
            except OSError:  # nexgen: allow reason="chmod failure on non-POSIX tempdir is ignored"
                pass

        out(t("Connecting node to gateway via 'openclaw connect --service --session-host' (protected target file)..."))
        cmd = ["openclaw", "connect", "--service", "--session-host", "--target-file", str(target_file)]
        res = run_fn(cmd)
        if res.returncode != 0:
            out(t("Node connect failed (exit code {code}).", code=res.returncode))
            return False
        out(t("Node connect command completed."))
        return True
    finally:
        target_file.unlink(missing_ok=True)


def check_node_connected(
    runner: Callable[[list[str]], RunResult] | None = None,
    cmd: list[str] | None = None,
) -> bool:
    """Queries node status via documented 'openclaw node status --json'."""
    run_fn = runner or _default_runner
    run_cmd = cmd or ["openclaw", "node", "status", "--json"]
    try:
        res = run_fn(run_cmd)
        if res.returncode != 0 or not res.stdout.strip():
            return False
        try:
            data = json.loads(res.stdout)
            if isinstance(data, dict):
                status = str(data.get("status") or "").strip().lower()
                connected = data.get("connected")
                if connected is not None:
                    return bool(connected)
                return status in ("connected", "running", "online")
        except json.JSONDecodeError:
            out_lower = res.stdout.lower()
            return ("connected" in out_lower or "running" in out_lower) and "not connected" not in out_lower
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
