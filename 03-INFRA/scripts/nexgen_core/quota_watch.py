"""The two quota alerts, raised from the beat that already runs every hour.

One job: take the percentages `quota.percentage_rows` knows and hand each one
that crossed a threshold to the megaphone, once per provider, window and band.
The band is part of the key on purpose: keying on the provider alone would let
the 80% alert debounce the 95% one out of existence, which is the alert that
matters most.

Only the cheap readings are consulted. Codex answers from the last kilobyte of
its newest session and the harness archive answers one indexed query; the Claude
Code transcripts are hundreds of megabytes and are read by `nexgen quota` when a
person asks, never by a timer.

The debounce is the megaphone's own (four hours). A window that resets and
climbs back to the same band inside that period stays quiet until it expires:
one missed repeat is cheaper than an alert every hour on a plan that is simply
full.
"""
from __future__ import annotations

from pathlib import Path

from nexgen_core import quota
from nexgen_core.i18n import t
from nexgen_core.megaphone import Megaphone

#: The prefix of every debounce key this module writes, so they are recognisable
#: in the megaphone's state file and cannot collide with the guard's keys.
ALERT_KEY_PREFIX = "quota"


def check_and_alert(*, home: Path | None = None, vault: Path | None = None,
                    now: float | None = None, megaphone: Megaphone | None = None) -> dict:
    """Raises the alert for every band a known percentage has reached.

    Returns which percentages were judged, so the beat's own answer says what
    this step saw without claiming anything about delivery: `send_alert`
    reports True for a debounced alert too.
    """
    phone = megaphone or Megaphone()
    rows, _notes = quota.percentage_rows(home=home, vault=vault, now=now)
    judged: list[str] = []
    for row in rows:
        band = band_of(row.percent)
        if band is None:
            continue
        key = f"{ALERT_KEY_PREFIX}:{row.provider}:{row.window}:{band:.0f}"
        judged.append(key)
        phone.send_alert(
            title=t("Quota at {percent}%: {provider}", percent=f"{row.percent:.0f}", provider=row.provider),
            message=_message(row, now),
            action=t("Run 'nexgen quota' to see every window."),
            alert_key=key,
        )
    return {"ok": True, "judged": judged}


def band_of(percent: float | None) -> float | None:
    """The highest threshold `percent` has reached, or None when it is below all."""
    reached = [limit for limit in quota.ALERT_AT if percent is not None and percent >= limit]
    return reached[-1] if reached else None


def _message(row: quota.QuotaRow, now: float | None) -> str:
    """The sentence the alert carries, with the reset when the provider gave one."""
    moment = now if now is not None else (row.read_at or 0.0)
    if row.resets_at:
        return t(
            "{provider} has used {percent}% of its {window} window; it resets in {age}.",
            provider=row.provider, percent=f"{(row.percent or 0.0):.0f}", window=row.window,
            age=quota.age_text(max(0.0, row.resets_at - moment)),
        )
    return t(
        "{provider} has used {percent}% of its {window} window.",
        provider=row.provider, percent=f"{(row.percent or 0.0):.0f}", window=row.window,
    )
