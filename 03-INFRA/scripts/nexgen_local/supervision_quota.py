"""The quota a unit is started under: the reading before dispatch, and the row it leaves.

One job: before the supervisor starts a unit on a lane, read the cheap quota
readings for that lane's plan once and say whether another candidate should run
it instead.

A reading that is current and at or above the line the owner declared under
`failover.block_at` means the plan is about to stop answering, so the unit goes
to the next candidate `routing_tiers` already knows, and the whole plan goes with
it: a provider at that line is spent for every model it serves, exactly as the
report's own handover says. The journal records the reading, the decision and
where the unit went, so a person reading `sup status` later can see that the
gauge was looked at. The line is the plugin's number and not a second one: the
same file feeds `before_model_resolve`, and a supervisor that kept its own
threshold would dispatch to a plan the plugin had already stopped using.

Nothing here blocks work on a doubt. A reading that is stale, cached, missing or
unreadable is `quota unknown` and the unit starts where it was going to start:
the engine has never invented a percentage, and a guess is a worse reason to
stop a unit than no reason at all. Only the cheap readers answer here
(`quota.percentage_rows`): no transcript is opened, and the optional backend
answers from the cache it shares with the hourly beat, one run at most per
interval. A headless unit moves only to a harness that can open its first
session without a window; otherwise the move is journaled as not made instead
of failing later on a harness the owner never asked for.
"""
from __future__ import annotations

from nexgen_core import quota, quota_policy

from . import routing_tiers
from . import supervision_state as state

#: The plan each harness runs, spelled the way the report spells it, for a
#: candidate that names no `provider/model`. `opencode` is absent on purpose: it
#: serves two plans, and a candidate without `opencode-go/` or `opencode/` cannot
#: say which one it would spend. An unknown lane is not guessed at.
HARNESS_PLANS = {
    "claude": "claude",
    "codex": "codex",
    "antigravity": "antigravity",
    "agy": "antigravity",
}


def gate_unit(task_id: str, unit: str, model: str, harness: str, *, limited: bool = False,
              rows: list[quota.QuotaRow] | None = None, headless: bool = False,
              policy: quota_policy.Failover | None = None) -> tuple[str, str, str]:
    """The lane this unit starts on once its plan has been read, and the note to print.

    Returns the model, the harness and one line saying what happened; the line is
    empty when the plan is fine, so a healthy dispatch prints nothing new. The
    journal always learns about a move or an unknown reading, which is the point:
    a gauge nobody writes down is a gauge nobody looked at.

    `policy` is the owner's file as the caller already read it, and is loaded here
    when it is not passed; `headless` says the unit will run as a detached turn
    with no window to open, which is the one mode where a change of harness cannot
    be carried out.
    """
    row = lane_reading(model, harness, rows=rows)
    if row is None or row.percent is None:
        reason = "quota unknown: nessuna lettura corrente per questa corsia, si parte comunque"
        _journal(task_id, unit, model, harness, row, decision="quota unknown", reason=reason)
        return model, harness, reason
    block_at = (policy or quota_policy.load()[0]).block_at
    if row.percent < block_at:
        return model, harness, ""
    percent = f"{row.percent:.0f}"
    candidate = _next_candidate(model, limited=limited)
    if candidate is None:
        reason = f"quota al {percent}% ({row.window}): nessun altro candidato, si parte qui"
        _journal(task_id, unit, model, harness, row, decision="nessun candidato", reason=reason)
        return model, harness, reason
    if headless and not _opens_headless(str(candidate["harness"])):
        # A headless turn opens its own session, and a harness without a headless
        # form cannot: the move is recorded as not made instead of refused later in
        # the name of a harness the owner never asked for.
        reason = (f"quota al {percent}% ({row.window}): {candidate['harness']} non sa partire "
                  "senza finestra, resto qui")
        _journal(task_id, unit, model, harness, row, decision="headless", reason=reason)
        return model, harness, reason
    reason = f"quota al {percent}% ({row.window}): passa a {candidate['model']}"
    _journal(task_id, unit, model, harness, row, decision="spostata",
             reason=reason, next_model=str(candidate["model"]))
    return str(candidate["model"]), str(candidate["harness"]), reason


def _opens_headless(name: str) -> bool:
    """Whether this harness can open a unit's first session with no window."""
    from . import supervision_adapters as adapters

    return adapters.supports(name, "headless")


def lane_reading(model: str, harness: str, *, rows: list[quota.QuotaRow] | None = None) -> quota.QuotaRow | None:
    """The worst reading of the plan this lane runs on, or None when nothing describes it.

    `rows` is the report's own list, injected by a test; otherwise the cheap
    readers answer here. The plan is the model's own `provider/model` prefix when
    it has one, and otherwise the plan its harness runs: a bare `gemini-3.8-flash`
    on `antigravity` spends Antigravity's credits, and the row says so.
    """
    if rows is None:
        rows, _notes = quota.percentage_rows()
    provider = quota_policy.provider_of(model)
    family = "" if provider else HARNESS_PLANS.get(harness.casefold(), "")
    if not provider and not family:
        return None
    mine = [row for row in rows if _belongs(row, provider, family)]
    if not mine:
        return None
    return max(mine, key=_line_percent)


def _belongs(row: quota.QuotaRow, provider: str, family: str) -> bool:
    """Whether this row describes the plan the lane would spend."""
    if provider:
        return row.provider_id == provider
    return bool(family) and row.provider.casefold().startswith(family)


def _line_percent(row: quota.QuotaRow) -> float:
    """The percentage a decision can act on, with no percentage below every real one."""
    return row.percent if row.percent is not None else -1.0


def _next_candidate(model: str, *, limited: bool) -> dict | None:
    """The next worker candidate, or None when the owner's file has nowhere else to go.

    The models that share this plan are excluded with it, because switching from
    one spent plan to another model of the same plan is not a fallback. The six-hour
    exhaustion state is left alone: it belongs to a failure that really happened,
    and a window that resets in ten minutes must not be marked for six hours.
    """
    provider = quota_policy.provider_of(model)
    exclude = {model}
    if provider:
        exclude |= {other for other in routing_tiers.allowed_for()
                    if quota_policy.provider_of(other) == provider}
    try:
        return routing_tiers.pick("worker", allow_limited=limited, exclude=exclude)
    except routing_tiers.RoutingTiersError:  # nexgen: allow reason="no next candidate is a lane with nowhere to go, and the unit starts where it is with the journal saying so"
        return None


def _journal(task_id: str, unit: str, model: str, harness: str, row: quota.QuotaRow | None, *,
             decision: str, reason: str, next_model: str = "") -> None:
    """One `unit_quota` row: the reading this dispatch was decided on, and what was decided."""
    state.append_event(
        task_id, "unit_quota", unit=unit, harness=harness, model=model,
        provider=quota_policy.provider_of(model) or None,
        window=None if row is None else row.window,
        percent=None if row is None else row.percent,
        state=None if row is None else row.state,
        decision=decision, reason=reason, next_model=next_model or None,
    )
