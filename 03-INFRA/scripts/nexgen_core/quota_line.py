"""The one line a session start reads: what is left, worst window first.

One job: turn the cheap readings `quota.percentage_rows` already knows into the
line a session's first prompt carries, and print it for the hook that asks.

It is the report's own numbers in the space a session's first second can afford:
a plan that can be judged shows its highest current percentage, the moment it
resets and, when the number is older than the interval every caller shares, how
old it is. A plan whose reading is stale or cached says so in one word instead of
showing a number nobody may act on, and a plan with no percentage at all is left
out, because spend without a declared ceiling is not a quota. A machine that can
read nothing prints no line at all, because an empty gauge is not a reading.

This module never buys a run of the optional backend: it shows that backend's last
answer while the owner's own policy still trusts it, so asking for the line cannot
make a session start wait on a network call. `docs/quota.md` is where the rule and
the hook's three-second cap are written down.
"""
from __future__ import annotations

import time
from collections.abc import Iterable
from datetime import datetime

from nexgen_core import quota, quota_policy, quota_tokscale
from nexgen_core.i18n import t


def main() -> int:
    """Prints the line, or nothing when this machine cannot read a single plan. Always exit 0.

    Exit 0 is the contract the hook reads: it decodes one line and treats any
    failure as "nothing to show". A reader that raises on something this module
    never saw — a corrupt archive, an i18n lookup, a shim that cannot start —
    must therefore cost the line, not the session start it is decorating.
    """
    try:
        text = _line()
    except Exception:  # nexgen: allow reason="the hook treats a failing exit as no line at all, so an unexpected failure must still exit 0: the report itself says why when it is asked for one"
        return 0
    if text:
        print(text)
    return 0


def _line() -> str:
    """The line to print, from the policy this machine declares and the cheap readings."""
    policy, _unusable = quota_policy.load()
    moment = time.time()
    rows, _notes = quota.percentage_rows(now=moment, backend_max_age=float(policy.state_max_age_seconds))
    return session_line(rows, now=moment)


def session_line(rows: Iterable[quota.QuotaRow], *, now: float | None = None) -> str:
    """The line itself: the worst window of each plan, in the order the report lists them.

    A plan with no percentage and no stale reading is left out: that is spend without
    a declared ceiling, or a plan this machine never measures, and the line is the
    space where a person reads what can be judged, not the full report.
    """
    moment = time.time() if now is None else float(now)
    order: list[str] = []
    worst: dict[str, quota.QuotaRow] = {}
    for row in rows:
        if row.percent is None and row.state not in ("stale", quota.STATE_CACHED):
            continue
        if row.provider not in worst:
            order.append(row.provider)
            worst[row.provider] = row
        elif _worse(row, worst[row.provider]):
            worst[row.provider] = row
    if not order:
        return ""
    return t("Quota: {readings}",
             readings=" · ".join(_line_item(worst[name], moment) for name in order))


def _worse(candidate: quota.QuotaRow, current: quota.QuotaRow) -> bool:
    """Whether `candidate` is the window a session line should show for its plan."""
    return _line_percent(candidate) > _line_percent(current)


def _line_percent(row: quota.QuotaRow) -> float:
    """The percentage a line can act on, with no percentage below every real one."""
    return row.percent if row.percent is not None else -1.0


def _line_item(row: quota.QuotaRow, now: float) -> str:
    """One plan on the line: its worst window, its reset, its age, or the word for its gap."""
    if row.percent is None:
        return t("{provider} stale", provider=row.provider)
    item = t(
        "{provider} {window} {percent}",
        provider=row.provider, window=row.window, percent=quota.percent_text(row.percent),
    )
    notes: list[str] = []
    if row.resets_at:
        notes.append(t("reset {clock}", clock=_clock(row.resets_at)))
    if row.read_at is not None and now - row.read_at > quota_tokscale.CACHE_TTL_SECONDS:
        # Beyond the interval every caller shares, this number came from a cache:
        # the age is what keeps it from reading as a fresh measurement.
        notes.append(t("read {age} ago", age=quota.age_text(now - row.read_at)))
    if not notes:
        return item
    return t("{item} ({notes})", item=item, notes=", ".join(notes))


def _clock(moment: float) -> str:
    """A moment as the hour and minute a person reads: 18:00."""
    return datetime.fromtimestamp(moment).strftime("%H:%M")
