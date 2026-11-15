# nexgen: one-job reason="prove a gateway is alive and useful after a version change, in one command a person or CI can run"
"""The smoke test a pin needs: is the gateway up, and does it still answer?

A pinned version without a smoke test is a promise nobody checked. The engine
already had a doctor for the configuration and a health probe for the gateway, and
neither of them asks the only question that matters after an upgrade: does a turn
still come back? This runs the six things that answer it, in order, and stops
nothing else.

It orchestrates, it does not reimplement: `openclaw config validate`, `openclaw
mcp doctor` and `openclaw agent` are OpenClaw's own commands, and the gateway
probe is the one the engine already uses.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
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


def _check_version(runner: Run, out: Callable[[str], None]) -> bool:
    """Verify that OpenClaw is installed and prints its version."""
    version = runner(["openclaw", "--version"], READ_TIMEOUT_S)
    ok = version.returncode == 0 and bool((version.stdout or "").strip())
    detail = (version.stdout or version.stderr or "").strip().splitlines()[0] if (version.stdout or version.stderr) else ""
    _line(out, ok, t("OpenClaw is installed"), detail)
    return ok


def _check_config(runner: Run, out: Callable[[str], None]) -> bool:
    """Verify that OpenClaw configuration validates."""
    validated = runner(["openclaw", "config", "validate"], READ_TIMEOUT_S)
    ok = validated.returncode == 0
    detail = "" if ok else (validated.stdout or validated.stderr or "").strip().splitlines()[-1:]
    _line(out, ok, t("the configuration is valid"), detail if isinstance(detail, str) else " ".join(detail))
    return ok


def _check_gateway(health_fn: Callable[[], Any] | None, out: Callable[[str], None]) -> bool:
    """Verify that the OpenClaw gateway answers RPC health probe."""
    probe = health_fn
    if probe is None:
        from nexgen_core.openclaw.gateway_rpc import health as probe  # type: ignore[assignment]
    try:
        probe()
        gateway_ok, gateway_detail = True, ""
    except Exception as exc:  # nexgen: allow reason="a gateway that does not answer is the failure this check reports, and the sentence is its evidence"
        gateway_ok, gateway_detail = False, str(exc)
    _line(out, gateway_ok, t("the gateway answers"), gateway_detail)
    return gateway_ok


def _check_mcp(runner: Run, out: Callable[[str], None]) -> bool:
    """Verify that every mounted MCP server passes OpenClaw mcp doctor."""
    mcp = runner(["openclaw", "mcp", "doctor"], SERVICE_TIMEOUT_S)
    ok = mcp.returncode == 0
    mcp_detail = "" if ok else ((mcp.stdout or "") + (mcp.stderr or "")).strip().splitlines()[-1:] or ""
    _line(out, ok, t("every mounted MCP server is usable"),
          mcp_detail if isinstance(mcp_detail, str) else " ".join(mcp_detail))
    return ok


def _check_turn(runner: Run, agent: str, out: Callable[[str], None]) -> bool:
    """Send probe message and verify that the agent returns an answer."""
    turn = runner(["openclaw", "agent", "--agent", agent, "--message", PROBE_MESSAGE], TURN_TIMEOUT_S)
    answer = (turn.stdout or "").strip()
    ok = turn.returncode == 0 and bool(answer)
    detail = "" if ok else ((turn.stderr or "").strip().splitlines()[-1] if turn.stderr else "")
    _line(out, ok, t("agent {agent} answers a short turn", agent=agent), detail)
    return ok


def _agent_runs_on_claude_code(agent: str) -> bool | None:
    """Whether OpenClaw would run this agent on Claude Code; None when the file cannot say.

    The difference matters for the sentence a person reads: an agent that does run there
    and wrote no transcript is a session this check could not find, while an agent on
    `agy`, Codex or a direct model never writes one at all.
    """
    from nexgen_core.openclaw import background
    from nexgen_core.openclaw.config_file import read_config
    from nexgen_core.paths import openclaw_config

    try:
        config, failure = read_config(openclaw_config())
    except OSError:  # nexgen: allow reason="a configuration that cannot be read says nothing about the runtime"
        return None
    if failure is not None or config is None:
        return None
    ref = background.agent_model_refs(config).get(agent)
    if not ref:
        return None
    return background.cli_harness_runtime(config, ref, agent) == "claude-cli"


def _check_first_prompt_budget(
    claude_dir: Path | None,
    start_time: float,
    budget: int | None,
    out: Callable[[str], None],
    *,
    agent: str = "main",
    decides: bool = True,
) -> bool:
    """Measure the transcript this turn started, and judge it against the budget.

    `decides` is what separates two questions this one number cannot answer. A person
    asking `nexgen openclaw smoke` wants the budget to fail the run; the version gate
    asks whether the release works, and rolling a good upgrade back because a chat was
    large is the mistake this flag removes. There the line is a warning and the run's
    verdict comes from the five functional checks.
    """
    from nexgen_core.openclaw.context_budget import (
        claude_home,
        read_claude_first_prompt,
        resolved_budget,
        transcript_started_since,
    )

    base = claude_dir if claude_dir is not None else claude_home()
    ceiling = resolved_budget(budget)
    session = transcript_started_since(base, start_time)

    if session is None:
        runs_here = _agent_runs_on_claude_code(agent)
        reason = (t("no transcript for this turn")
                  if runs_here is not False
                  else t("agent {agent} does not run on Claude Code", agent=agent))
        out(f"SKIP  {t('first prompt context budget: not measured ({reason})', reason=reason)}")
        return True

    tokens = read_claude_first_prompt(session.path)
    if tokens is None:
        out(f"SKIP  {t('first prompt context budget: not measured (no tokens recorded)')}")
        return True

    ok = tokens <= ceiling
    detail = f"{tokens:,} <= {ceiling:,}" if ok else f"{tokens:,} > {ceiling:,}"
    if ok or decides:
        _line(out, ok, t("first prompt is within context budget"), detail)
    else:
        # Not a PASS either: the number was measured and is over the ceiling, and only
        # the doctor's own check is allowed to turn that into a fault.
        out(f"WARN  {t('first prompt is over the context budget, which is not a verdict on the release')}: "
            f"{detail}")
    return ok


def run_smoke(
    *,
    run: Run | None = None,
    health_fn: Callable[[], Any] | None = None,
    agent: str = "main",
    out: Callable[[str], None] = print,
    which: Callable[[str], str | None] | None = None,
    claude_dir: Path | None = None,
    budget: int | None = None,
    budget_decides: bool = True,
) -> int:
    """Runs the six checks, prints one line each, and says whether the gateway is usable.

    `budget_decides=False` keeps the first-prompt measurement but takes it out of the
    verdict, which is what a caller proving a release needs: a token count is a health
    metric of this machine, not evidence about OpenClaw.
    """
    runner = run or (lambda argv, timeout: run_command(argv, timeout, which))
    failures = 0

    failures += 0 if _check_version(runner, out) else 1
    failures += 0 if _check_config(runner, out) else 1
    failures += 0 if _check_gateway(health_fn, out) else 1
    failures += 0 if _check_mcp(runner, out) else 1

    turn_start = time.time()
    failures += 0 if _check_turn(runner, agent, out) else 1
    within = _check_first_prompt_budget(
        claude_dir, turn_start, budget, out, agent=agent, decides=budget_decides,
    )
    if budget_decides:
        failures += 0 if within else 1

    if failures:
        out(t("{count} check(s) failed: the gateway is not usable as it stands.", count=failures))
        return 1
    out(t("The gateway is up and answering."))
    return 0
