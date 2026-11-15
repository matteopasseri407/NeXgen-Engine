"""What is left of each subscription this machine uses, one row per window.

One job: turn the readings that exist on this machine into rows a person can
read, and say plainly which ones cannot be measured. Codex reports its own
percentage and the engine repeats it; the harness archive gives the spend of the
paid lanes, split by provider; Claude Code gives token counters and no
percentage at all, because its plan usage is not in any local file.

No number here is guessed. A paid lane shows a percentage only when a ceiling
for that window was declared in `quota.yaml`; without one it shows what was
spent and says the ceiling is missing. A percentage older than the window it
describes is dropped, because that window has already reset. Antigravity's log
is written only while its IDE runs, so its monthly reading also has to be a day
old or less: past that it stops describing today, keeps its age, and loses the
percentage a handover would act on. A live reading a provider refused to refresh
is treated the same way, by `quota_tokscale`, and reaches this report as
`STATE_CACHED`: the number, its age, and no percentage.

Each row also carries the model that lane hands over to, when the owner declared
a fallback policy in the same file: the report is where a person looks to know
what happens when a plan stops answering, and the answer belongs next to the
number that raises the question.

The report is also the answer `/quota` gives inside OpenClaw, so it carries its
own rendered lines: the plugin forwards text and never formats a number.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

import yaml

from nexgen_core import quota_antigravity, quota_claude, quota_codex, quota_policy, quota_tokscale
from nexgen_core.i18n import t
from nexgen_core.paths import codex_home, resolve_home, resolve_vault_data

#: Where a machine declares the ceilings it knows: relative to the Vault.
QUOTA_SUBPATH = ("03-INFRA", "agent-universal-layer", "quota.yaml")

#: The percentages that raise an alert, lowest first. One source for the watch
#: and for the doctor, so the two can never disagree about what "high" means.
ALERT_AT = (80.0, 95.0)

#: The state of a reading a live source could not refresh: the number and its age
#: are still shown, and the percentage is not, because a handover must not act on
#: a measurement of a window that may already be over. `quota_tokscale` sets it
#: for a throttled provider, and `_row_text` is the only place that reads it.
STATE_CACHED = "cached"

#: How old Antigravity's reading may be before this report stops calling it
#: current. Its window is a month long, but the log is only rewritten while the
#: IDE runs, so a percentage that is still inside its own window can be weeks old
#: and describe a month that started before the last reset. A day is the longest a
#: person can read here and still be looking at today.
ANTIGRAVITY_MAX_READING_AGE_SECONDS = 24 * 3600

#: The provider ids the harness archive uses, and the name a person reads.
PROVIDER_NAMES = {"opencode-go": "OpenCode Go", "opencode": "OpenCode Zen"}

#: The id Claude Code's plan is reached by in a fallback chain, which both the
#: token rows and the live reading of the same plan carry.
CLAUDE_PROVIDER_ID = "anthropic"

#: The windows the report shows for the harness archive, in reading order.
SPEND_WINDOWS = ("5h", "7d", "30d")
#: The windows the report shows for Claude Code.
CLAUDE_WINDOWS = ("5h", "7d")


@dataclass(frozen=True)
class QuotaRow:
    """One window of one provider: what was used, and how much of it is left.

    `percent` is set only when it is a real percentage of a real ceiling, so a
    consumer may treat it as "this provider can be judged". `used` is always
    the provider's own number, in `unit`. `provider_id` is the id the provider has
    in the models a harness names (`opencode-go`, `openai`, `anthropic`), which is
    what a fallback chain speaks; `provider` is only what a person reads.
    """

    provider: str
    provider_id: str = ""
    window: str = ""
    used: float | None = None
    unit: str = ""
    percent: float | None = None
    ceiling: float | None = None
    resets_at: float | None = None
    read_at: float | None = None
    state: str = "unknown"
    note: str = ""
    fallback: str = ""

    def to_dict(self, now: float | None = None) -> dict:
        """The row as JSON: times in ISO, plus the age a consumer would compute."""
        return {
            "provider": self.provider,
            "provider_id": self.provider_id,
            "window": self.window,
            "unit": self.unit,
            "used": self.used,
            "percent": self.percent,
            "ceiling": self.ceiling,
            "resets_at": _iso(self.resets_at),
            "read_at": _iso(self.read_at),
            "age_seconds": _age_seconds(self.read_at, now),
            "state": self.state,
            "note": self.note,
            "fallback": self.fallback,
        }


@dataclass(frozen=True)
class QuotaReport:
    """Every row this machine can answer, and the lines a person reads."""

    read_at: float
    rows: tuple[QuotaRow, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def lines(self) -> list[str]:
        """One line per row, then one per source that could not answer."""
        return [render_row(row, self.read_at) for row in self.rows] + list(self.notes)

    def to_dict(self) -> dict:
        return {
            "read_at": _iso(self.read_at),
            "rows": [row.to_dict(self.read_at) for row in self.rows],
            "lines": self.lines,
            "notes": list(self.notes),
        }


def build_report(*, home: Path | None = None, vault: Path | None = None,
                 now: float | None = None) -> QuotaReport:
    """Everything the report shows, including the source that costs seconds.

    Claude Code's transcripts are read here and nowhere else: a week of them is
    hundreds of megabytes, which is affordable for a command someone typed and
    not for the hourly beat or the doctor. Those two ask `percentage_rows`.

    The tokens are read only when nothing above already answers that plan: a live
    reading is the answer for the plan it measured, and counting the same plan's
    tokens beside it would print one provider twice, from two readers that
    disagree about what the number means.
    """
    moment = time.time() if now is None else float(now)
    rows, notes = percentage_rows(home=home, vault=vault, now=moment)
    if not any(row.provider_id == CLAUDE_PROVIDER_ID for row in rows):
        usage = quota_claude.read_usage(resolve_home(home) / ".claude", now=moment)
        if usage.note:
            notes.append(usage.note)
        rows.extend(_claude_rows(usage, moment))
    if not any(row.provider == "Antigravity" for row in rows):
        rows.append(QuotaRow(
            provider="Antigravity", state="unknown",
            note=t("not measured"),
        ))
    policy, unusable = quota_policy.load(home, vault=vault)
    return QuotaReport(read_at=moment, rows=tuple(with_handovers(rows, policy)),
                       notes=tuple(notes + unusable))


def with_handovers(rows: list[QuotaRow], policy: quota_policy.Failover) -> list[QuotaRow]:
    """Each row with the model it hands over to, when the policy names one.

    The chain read here is the assistant's: it is the agent a person talks to, and
    the shared chain applies to it unless the policy overrides that one agent.
    """
    chain = policy.for_agent("main")
    if not chain:
        return rows
    return [replace(row, fallback=quota_policy.handover_to(chain, row.provider_id)) for row in rows]


def percentage_rows(*, home: Path | None = None, vault: Path | None = None,
                    now: float | None = None,
                    backend_max_age: float | None = None) -> tuple[list[QuotaRow], list[str]]:
    """The rows whose percentage is a real number, and why some are missing.

    This is the cheap half of the report: Codex reads a few kilobytes from the
    tail of its newest session, the harness archive answers one query. No
    transcript is opened, so it is safe to call from a timer. The optional
    tokscale backend is asked here too, and it answers from a cache that is a few
    minutes old, because one run of it costs seconds and asks the network.

    `backend_max_age` reads that backend from its last answer only, never from a
    run: it is what a caller with a deadline of its own states, and a backend
    whose cache is older than that simply has no rows here.
    """
    moment = time.time() if now is None else float(now)
    rows: list[QuotaRow] = []
    notes: list[str] = []
    external = quota_tokscale.read_usage(home, config=limits_path(home, vault=vault), now=moment,
                                         max_age=backend_max_age)
    rows.extend(_codex_rows(home, moment, notes))
    rows.extend(_opencode_rows(home, vault, moment, notes))
    rows.extend(_antigravity_rows(home, moment, notes))
    return quota_tokscale.report_rows(external, rows, notes), notes


def render_row(row: QuotaRow, now: float) -> str:
    """One row as the sentence a person reads, in the language they read."""
    base = _row_text(row, now)
    if not row.fallback:
        return base
    return t("{row}, then {model}", row=base, model=row.fallback)


def _row_text(row: QuotaRow, now: float) -> str:
    """The row without its handover: what was used, how much is left, how old it is."""
    if row.state == "unknown":
        return t("{provider}: not measured", provider=row.provider)
    age = age_text(_age_seconds(row.read_at, now) or 0.0)
    if row.state == "stale":
        return t(
            "{provider}: the {window} reading is {age} old, too old to say what is left",
            provider=row.provider, window=row.window, age=age,
        )
    if row.state == STATE_CACHED:
        return t(
            "{provider}: {percent} of the {window} window in the last reading ({age}), and it was not refreshed",
            provider=row.provider, percent=percent_text(row.used), window=row.window,
            age=t("read {age} ago", age=age),
        )
    if row.unit == "percent":
        line = t(
            "{provider}: {percent} of the {window} window used ({age})",
            provider=row.provider, percent=percent_text(row.used), window=row.window, age=t("read {age} ago", age=age),
        )
        if row.resets_at:
            return f"{line}, " + t("resets in {age}", age=age_text(max(0.0, row.resets_at - now)))
        return line
    if row.unit == "usd":
        amount = f"{row.used or 0.0:.2f} $"
        if row.percent is None:
            return t(
                "{provider}: {amount} spent in the last {window}, no declared ceiling ({age})",
                provider=row.provider, amount=amount, window=row.window,
                age=t("read {age} ago", age=age),
            )
        return t(
            "{provider}: {amount} of {ceiling} in the last {window} ({percent}, {age})",
            provider=row.provider, amount=amount, ceiling=f"{(row.ceiling or 0.0):.2f} $", window=row.window,
            percent=percent_text(row.percent), age=t("read {age} ago", age=age),
        )
    if row.unit == "tokens":
        return t(
            "{provider}: {tokens} tokens in the last {window} ({age}), the limit is not readable on this machine",
            provider=row.provider, tokens=f"{int(row.used or 0):,}", window=row.window,
            age=t("read {age} ago", age=age),
        )
    return f"{row.provider}: {row.note}" if row.note else row.provider


def limits_path(home: Path | None = None, *, vault: Path | None = None) -> Path:
    """Where the declared ceilings live: `<vault>/03-INFRA/agent-universal-layer/quota.yaml`."""
    return (vault or resolve_vault_data(home)).joinpath(*QUOTA_SUBPATH)


def load_limits(path: Path) -> tuple[dict[str, dict[str, float]], list[str]]:
    """The declared ceilings per provider and window, and what could not be used.

    The file is optional: without it every paid lane reports spend and no
    percentage. A file that cannot be read costs the percentages, never the
    command, and says so once.
    """
    if not path.is_file():
        return {}, []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        return {}, [t("The declared limits in {path} cannot be read: {error}", path=path, error=exc)]
    declared = data.get("limits") if isinstance(data, dict) else None
    if not isinstance(declared, dict):
        return {}, []
    limits: dict[str, dict[str, float]] = {}
    ignored: list[str] = []
    for provider, windows in declared.items():
        if not isinstance(windows, dict):
            ignored.append(str(provider))
            continue
        for window, amount in windows.items():
            if isinstance(amount, (int, float)) and not isinstance(amount, bool) and amount > 0:
                limits.setdefault(str(provider), {})[str(window)] = float(amount)
            else:
                ignored.append(f"{provider}/{window}")
    return limits, [
        t("The declared limit for {entry} is not a positive number: ignored.", entry=entry)
        for entry in ignored
    ]


def main(as_json: bool = False) -> int:
    """Prints the report: a source that cannot be read is an answer here, never an error."""
    report = build_report()
    if as_json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        for line in report.lines:
            print(line)
    return 0


# ---------------------------------------------------------------- the sources


def _codex_rows(home: Path | None, now: float, notes: list[str]) -> list[QuotaRow]:
    """Codex's own percentage, one row per window, dropped when it has expired."""
    usage = quota_codex.read_usage(codex_home(home))
    if not usage.known:
        if usage.note:
            notes.append(usage.note)
        return []
    provider = f"Codex ({usage.plan})" if usage.plan else "Codex"
    rows: list[QuotaRow] = []
    for limit in (usage.primary, usage.secondary):
        if limit is None:
            continue
        window = window_label(limit.window_minutes)
        stale = _expired(usage.read_at, limit.window_minutes, now)
        rows.append(QuotaRow(
            provider=provider,
            # Codex is reached in OpenClaw through the `openai` provider, whatever the
            # plan behind the login is called; a chain names `openai/<model>`.
            provider_id="openai",
            window=window,
            used=limit.used_percent,
            unit="percent",
            percent=None if stale else limit.used_percent,
            resets_at=float(limit.resets_at) if limit.resets_at else None,
            read_at=usage.read_at,
            state="stale" if stale else _state(limit.used_percent),
        ))
    return rows


def _opencode_rows(home: Path | None, vault: Path | None, now: float, notes: list[str]) -> list[QuotaRow]:
    """The harness archive's spend per provider, with a percentage only when declared.

    The arithmetic is the harness lane's own (`supervision_spend`): this module
    decides what a person reads, not how a cost is computed. A checkout without
    that lane is a real state (it is a separate package inside the engine), and it
    costs this one source, never the command. A session whose `model` names no
    provider is counted under that lane's own Italian placeholder, translated here
    like every other word on the row.
    """
    try:
        from nexgen_local import supervision_spend
    except ImportError:
        notes.append(t("OpenCode: the harness lane is not installed in this engine."))
        return []

    spend = supervision_spend.spend_by_provider(home=home, now=now)
    if spend["note"]:
        notes.append(t("OpenCode: spend is unknown, the harness archive could not be read."))
        return []
    limits, unusable = load_limits(limits_path(home, vault=vault))
    notes.extend(unusable)
    rows: list[QuotaRow] = []
    for provider, windows in sorted(spend["providers"].items()):
        declared = limits.get(provider, {})
        for window in SPEND_WINDOWS:
            counted = windows.get(window) or {}
            if not counted.get("sessions"):
                continue
            cost = float(counted.get("cost_usd") or 0.0)
            ceiling = declared.get(window)
            percent = round(100.0 * cost / ceiling, 1) if ceiling else None
            rows.append(QuotaRow(
                provider=t("(unknown)") if provider == supervision_spend.UNKNOWN_PROVIDER else PROVIDER_NAMES.get(provider, provider),
                provider_id=provider,
                window=window,
                used=round(cost, 2),
                unit="usd",
                percent=percent,
                ceiling=ceiling,
                read_at=now,
                state=_state(percent) if percent is not None else "ok",
            ))
    return rows


def _claude_rows(usage: quota_claude.ClaudeUsage, now: float) -> list[QuotaRow]:
    """Claude Code's tokens per window: consumption, never a percentage."""
    if not usage.known:
        return []
    return [
        QuotaRow(
            provider="Claude Code",
            provider_id=CLAUDE_PROVIDER_ID,
            window=window,
            used=float(usage.windows.get(window, {}).get("tokens") or 0),
            unit="tokens",
            read_at=usage.read_at,
            state="ok",
            note=t("the limit is not readable on this machine"),
        )
        for window in CLAUDE_WINDOWS
    ]


def _antigravity_rows(home: Path | None, now: float, notes: list[str]) -> list[QuotaRow]:
    """Antigravity's prompt credits, kept only while its log is a day old or less."""
    usage = quota_antigravity.read_usage(home=home, now=now)
    if not usage.known:
        return []
    window = "30d"
    stale = _older_than(usage.read_at, ANTIGRAVITY_MAX_READING_AGE_SECONDS, now)
    return [
        QuotaRow(
            provider="Antigravity",
            window=window,
            used=usage.prompt_credits_used_percent,
            unit="percent",
            percent=None if stale else usage.prompt_credits_used_percent,
            resets_at=float(usage.resets_at) if usage.resets_at else None,
            read_at=usage.read_at,
            state="stale" if stale else _state(usage.prompt_credits_used_percent),
        )
    ]


# ------------------------------------------------------------------- helpers


def _state(percent: float | None) -> str:
    """How a row reads: over the first threshold, or fine."""
    return "warn" if percent is not None and percent >= ALERT_AT[0] else "ok"


def _expired(read_at: float | None, window_minutes: int | None, now: float) -> bool:
    """Whether a reading is older than the window it describes.

    After that the window has certainly rolled over, so the percentage no
    longer describes what is left today. A reading with no moment of its own
    cannot be trusted with a percentage either.
    """
    if not window_minutes:
        return False
    return _older_than(read_at, window_minutes * 60, now)


def _older_than(read_at: float | None, max_age_seconds: float, now: float) -> bool:
    """Whether a reading is older than `max_age_seconds`, or carries no moment at all."""
    if read_at is None:
        return True
    return now - read_at > max_age_seconds


def percent_text(value: float | None) -> str:
    """A percentage as a person reads it: whole numbers unless it is not one."""
    if value is None:
        return "?"
    return f"{value:.0f}%" if abs(value - round(value)) < 0.05 else f"{value:.1f}%"


def window_label(minutes: int | None) -> str:
    """A window length as the short label a person reads: 5h, 7d, 12h."""
    if not minutes or minutes <= 0:
        return "?"
    if minutes % 1440 == 0:
        return f"{minutes // 1440}d"
    if minutes % 60 == 0:
        return f"{minutes // 60}h"
    return f"{minutes}m"


def _age_seconds(read_at: float | None, now: float | None) -> float | None:
    """How old a reading is, in seconds, or None when it carries no moment."""
    if read_at is None or now is None:
        return None
    return max(0.0, now - read_at)


def age_text(seconds: float) -> str:
    """A duration as the shortest form that is still exact enough: 4m, 3h 12m."""
    total = int(max(0.0, seconds))
    if total < 60:
        return "<1m"
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m"


def _iso(moment: float | None) -> str | None:
    """Epoch seconds as an ISO-8601 local moment, or None."""
    if moment is None:
        return None
    return datetime.fromtimestamp(moment).isoformat(timespec="seconds")
