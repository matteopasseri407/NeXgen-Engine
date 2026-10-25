# nexgen: one-job reason="prove a gateway is alive and useful after a version change, in one command a person or CI can run"
"""The smoke test a pin needs: is the gateway up, and does it still answer?

A pinned version without a smoke test is a promise nobody checked. The engine
already had a doctor for the configuration and a health probe for the gateway, and
neither of them asks the only question that matters after an upgrade: does a turn
still come back? This runs the five things that answer it, in order, and stops
nothing else.

It orchestrates, it does not reimplement: `openclaw config validate`, `openclaw
mcp doctor` and `openclaw agent` are OpenClaw's own commands, and the gateway
probe is the one the engine already uses.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nexgen_core.i18n import t
from nexgen_core.openclaw.node_cli import READ_TIMEOUT_S, SERVICE_TIMEOUT_S, Run, run_command

#: The words sent to the agent. Short on purpose: this measures a round trip, not a mind.
PROBE_MESSAGE = "Smoke test dell'engine: rispondi con una parola sola."

#: How long a turn may take before the smoke calls it a failure.
TURN_TIMEOUT_S = 180.0


def _line(out: Callable[[str], None], ok: bool, name: str, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    out(f"{mark}  {name}{(': ' + detail) if detail else ''}")


def run_smoke(
    *,
    run: Run | None = None,
    health_fn: Callable[[], Any] | None = None,
    agent: str = "main",
    out: Callable[[str], None] = print,
    which: Callable[[str], str | None] | None = None,
) -> int:
    """Runs the five checks, prints one line each, and says whether the gateway is usable."""
    runner = run or (lambda argv, timeout: run_command(argv, timeout, which))
    failures = 0

    version = runner(["openclaw", "--version"], READ_TIMEOUT_S)
    ok = version.returncode == 0 and bool((version.stdout or "").strip())
    _line(out, ok, t("OpenClaw is installed"),
          (version.stdout or version.stderr or "").strip().splitlines()[0] if (version.stdout or version.stderr) else "")
    failures += 0 if ok else 1

    validated = runner(["openclaw", "config", "validate"], READ_TIMEOUT_S)
    ok = validated.returncode == 0
    detail = "" if ok else (validated.stdout or validated.stderr or "").strip().splitlines()[-1:]
    _line(out, ok, t("the configuration is valid"), detail if isinstance(detail, str) else " ".join(detail))
    failures += 0 if ok else 1

    probe = health_fn
    if probe is None:
        from nexgen_core.openclaw.gateway_rpc import health as probe  # type: ignore[assignment]
    try:
        probe()
        gateway_ok, gateway_detail = True, ""
    except Exception as exc:  # nexgen: allow reason="a gateway that does not answer is the failure this check reports, and the sentence is its evidence"
        gateway_ok, gateway_detail = False, str(exc)
    _line(out, gateway_ok, t("the gateway answers"), gateway_detail)
    failures += 0 if gateway_ok else 1

    mcp = runner(["openclaw", "mcp", "doctor"], SERVICE_TIMEOUT_S)
    ok = mcp.returncode == 0
    mcp_detail = "" if ok else ((mcp.stdout or "") + (mcp.stderr or "")).strip().splitlines()[-1:] or ""
    _line(out, ok, t("every mounted MCP server is usable"),
          mcp_detail if isinstance(mcp_detail, str) else " ".join(mcp_detail))
    failures += 0 if ok else 1

    turn = runner(["openclaw", "agent", agent, "--message", PROBE_MESSAGE], TURN_TIMEOUT_S)
    answer = (turn.stdout or "").strip()
    ok = turn.returncode == 0 and bool(answer)
    _line(out, ok, t("agent {agent} answers a short turn", agent=agent),
          "" if ok else ((turn.stderr or "").strip().splitlines()[-1] if turn.stderr else ""))
    failures += 0 if ok else 1

    if failures:
        out(t("{count} check(s) failed: the gateway is not usable as it stands.", count=failures))
        return 1
    out(t("The gateway is up and answering."))
    return 0
