# nexgen: one-job reason="answer what OpenClaw spent per day, per session and per agent through the optional ccusage binary"
"""What OpenClaw spent, as the optional `ccusage` binary reads it.

One job: run `ccusage openclaw daily --json` and `ccusage openclaw session --json`
(npm `ccusage`, MIT) and hand back the tokens and dollars those two views report,
with every session attributed to the agent folder that owns its transcript, or one
sentence saying why there is no number.

Why this binary and not a reader of our own. OpenClaw writes one JSONL record per
assistant message and, since 2026.9.x, also keeps the same events in a per-agent
`openclaw-agent.sqlite`; the SQLite row is the one that carries the provider-billed
cost, and a JSONL scrape that ignores it silently misses the current week.
`ccusage` already merges both, prefers the SQLite row, and uses the cost OpenClaw
embedded, so its answer needs no network, no credential file and no price table of
ours. `docs/proposals/github-adoption.md` and `docs/openclaw.md` record the trade:
`daily` and `session` group by date and by session id only, so per-agent
attribution is the engine's half of the job, and a release that renames a field
costs the number rather than printing a half-understood one.

Which agent owns a session. ccusage keys a session on the JSONL filename stem and
OpenClaw writes `<state dir>/agents/<agentId>/sessions/<sessionId>.jsonl`, so the
folder that holds the file names the agent. A session whose file was deleted
entirely is unattributed rather than guessed: an archived transcript keeps its stem
in the name (`<id>.jsonl.deleted.<ts>...`), a vanished one leaves nothing.

A session counts whole, on the day of its last activity. `session` carries
`firstActivity` and `lastActivity` and no per-day split, so yesterday's per-agent
line is a split by "last active that day", never by "spent that day"; the day
totals themselves come from `daily`, which is exact.

Nothing is half-trusted. The output carries no schema version, so a shape this
module does not recognise is refused whole and named in one line. A missing
binary, a non-zero exit, a timeout and an unreadable answer are all one sentence at
the bottom of the report: `mode` turns the whole backend off, and a machine that
never installed it sees exactly what it saw before this file existed.

One run costs a second or two and reads local files, and the doctor asks for it, so
one answer is bought at most once per `CACHE_TTL_SECONDS` and cached in the
machine-local state directory with the date filter it was bought for. The run
starts in that directory, never in whatever repository the caller was working in.
The run itself lives in `nexgen_core.ccusage`, shared with the harness archive's
own reading, so the two see one binary and one set of spawn rules.
"""
from __future__ import annotations

import os
import tempfile
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path

from nexgen_core import ccusage
from nexgen_core.ccusage import FAILED, FORMAT, MISSING, TIMEOUT, FormatError
from nexgen_core.i18n import t
from nexgen_core.paths import resolve_state_dir
from nexgen_core.report import CheckOutcome, Severity

#: The one view this module reads, and the second one it needs for attribution.
DAILY_COMMAND = ("openclaw", "daily", "--json")
SESSION_COMMAND = ("openclaw", "session", "--json")

#: The two words `mode()` answers with.
AUTO = "auto"
OFF = ccusage.OFF

#: How long one answer is reused, from the module that owns the binary.
CACHE_TTL_SECONDS = ccusage.CACHE_TTL_SECONDS

#: Where the last answer waits, under the machine-local state directory.
CACHE_SUBPATH = ("nexgen", "ccusage-openclaw.json")

#: Where OpenClaw keeps its agent folders, under its own state directory.
AGENTS_SUBPATH = "agents"
SESSIONS_SUBPATH = "sessions"

#: The same switch for one run, so a sandbox or a scheduled job can silence the
#: backend without editing the file the owner maintains.
ENV_OVERRIDE = "NEXGEN_OPENCLAW_CCUSAGE"

#: The id the doctor prints for the one line this module adds.
CHECK_ID = "openclaw.spend"


@dataclass(frozen=True)
class DaySpend:
    """One day of OpenClaw as `daily` reports it: what it cost and what it read."""

    date: str
    tokens: int
    cost_usd: float
    models: tuple[str, ...] = ()


@dataclass(frozen=True)
class SessionSpend:
    """One session, the agent folder whose transcript it is, and what it cost.

    `agent` is empty when no folder on this machine holds a file with that session
    id: a transcript deleted whole leaves nothing to attribute, and naming an agent
    from a guess would put one agent's bill on another.
    """

    session_id: str
    agent: str
    tokens: int
    cost_usd: float
    last_activity: str | None = None


@dataclass(frozen=True)
class Spend:
    """Every day and session one answer carried, and why it may be empty."""

    read_at: float | None = None
    days: tuple[DaySpend, ...] = ()
    sessions: tuple[SessionSpend, ...] = ()
    since: str | None = None
    note: str = ""

    @property
    def known(self) -> bool:
        """Whether the backend answered with a number this report can show."""
        return bool(self.days) or bool(self.sessions)

    @property
    def tokens(self) -> int:
        """The tokens of every day the answer covers."""
        return sum(day.tokens for day in self.days)

    @property
    def cost_usd(self) -> float:
        """The dollars of every day the answer covers."""
        return sum(day.cost_usd for day in self.days)

    def by_agent(self) -> tuple[tuple[str, int, float], ...]:
        """Tokens and dollars per agent, dearest first, with `""` for unattributed."""
        spent: dict[str, list] = {}
        for session in self.sessions:
            totals = spent.setdefault(session.agent, [0, 0.0])
            totals[0] += session.tokens
            totals[1] += session.cost_usd
        return tuple(
            (agent, totals[0], totals[1])
            for agent, totals in sorted(spent.items(), key=lambda item: (-item[1][1], item[0]))
        )


def read_spend(
    home: Path | None = None,
    *,
    config: Path | None = None,
    state_dir: Path | None = None,
    now: float | None = None,
    since: str | None = None,
) -> Spend:
    """What OpenClaw spent, as `ccusage` reports it, or why it says nothing.

    `config` is the `quota.yaml` this machine declares, whose `backends` section can
    turn the backend off, and leaving it out reads the machine's own; `state_dir` is
    OpenClaw's own folder, read to name the agent that owns each session, and
    resolved from `OPENCLAW_STATE_DIR` when the caller does not pass it. `since` is
    the date filter `ccusage` documents, and it is part of what a cached answer is
    valid for: a different filter buys a run.

    One answer is reused for `CACHE_TTL_SECONDS`, whatever said it, so a failure is
    cached too and the next caller inside the interval does not pay for the same
    timeout twice.
    """
    moment = _time(now)
    if mode(_config(home) if config is None else config) == OFF:
        return Spend()
    since = _filter(since)
    stored = _cached(home, moment, since)
    if stored is None:
        stored = _store(home, moment, since, state_dir)
    return stored


def _config(home: Path | None) -> Path:
    """The `quota.yaml` this machine declares, resolved by the module that owns it.

    `quota.limits_path` is the one place that knows the file's location, and it is
    reached at the call so this module does not repeat the Vault's layout or close
    an import circle through `quota_policy`.
    """
    from nexgen_core import quota

    return quota.limits_path(home)


def mode(config: Path | None) -> str:
    """Whether this backend is asked at all: `auto` unless the file says `off`.

    The file is optional and so is the section, so a machine that never wrote a
    `backends:` line has the shipped behaviour; reading it is `ccusage.enabled`,
    which the harness archive's own reading obeys too, because one binary has one
    switch. `NEXGEN_OPENCLAW_CCUSAGE=off` says the same thing for one run of this
    backend, without editing the file: a scheduler, a script, or a test that must
    not read the bot's own transcripts.
    """
    if os.environ.get(ENV_OVERRIDE, "").strip().casefold() == OFF:
        return OFF
    return AUTO if ccusage.enabled(config) else OFF


def cache_path(home: Path | None = None) -> Path:
    """Where the last answer waits: machine-local state, never the engine checkout."""
    return resolve_state_dir(home).joinpath(*CACHE_SUBPATH)


def workdir(home: Path | None = None) -> Path:
    """The folder a run executes in: the machine's state dir, never the caller's directory.

    `ccusage` writes nothing on a read, but it does resolve `OPENCLAW_DIR` against
    the working directory and it is called from wherever a command happens to be
    typed. Starting it in the state directory keeps a relative path from ever
    meaning the repository a person was working in.
    """
    folder = cache_path(home).parent
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:  # nexgen: allow reason="an unwritable state dir cannot be the cwd, and the temp dir is the one folder every host can start a program in"
        return Path(tempfile.gettempdir())
    return folder


def parse_daily(text: str) -> tuple[DaySpend, ...]:
    """Every day `ccusage openclaw daily --json` printed, or a `FormatError`.

    Only the fields this module prints are required: the period, the total tokens
    and the total cost. The four token components are not shown anywhere, so a
    release that renames one of them cannot make a number here half-understood.
    `modelsUsed` is required because the report names the models a day read, and a
    missing list would otherwise be printed as "no model".
    """
    return tuple(_day(entry, index) for index, entry in enumerate(ccusage.document(text, "daily")))


def parse_sessions(text: str) -> tuple[SessionSpend, ...]:
    """Every session `ccusage openclaw session --json` printed, before attribution.

    The agent is not in the answer: `report.rs` groups by session id and drops the
    folder it read the session from, so `attribute` fills it in from this machine.
    """
    return tuple(_session(entry, index) for index, entry in enumerate(ccusage.document(text, "sessions")))


def session_agents(state_dir: Path | None) -> dict[str, str]:
    """The agent folder that owns each session id, from where OpenClaw writes it.

    One walk of `<state dir>/agents/*/sessions`, read-only: OpenClaw names the file
    after the session id, so the stem is the id `ccusage` keys on and the agent
    folder above it is the answer. An archived transcript keeps its stem in the name
    (`<id>.jsonl.deleted.<timestamp>...`), which is why the match is a prefix of the
    file name rather than the name itself. Two folders holding the same id is not a
    machine this engine creates; the first agent in sorted order wins and the answer
    stays deterministic.
    """
    if state_dir is None:
        return {}
    root = Path(state_dir) / AGENTS_SUBPATH
    try:
        folders = sorted(path for path in root.iterdir() if path.is_dir())
    except OSError:  # nexgen: allow reason="a state dir this module cannot list owns no session, so nothing is attributed and the numbers still print"
        return {}
    found: dict[str, str] = {}
    for folder in folders:
        try:
            files = list((folder / SESSIONS_SUBPATH).iterdir())
        except OSError:  # nexgen: allow reason="an agent with no readable sessions folder has no session to attribute"
            continue
        for path in files:
            name = path.name
            if ".jsonl" in name:
                found.setdefault(name.split(".jsonl", 1)[0], folder.name)
    return found


def attribute(sessions: tuple[SessionSpend, ...], agents: dict[str, str]) -> tuple[SessionSpend, ...]:
    """The same sessions, each carrying the agent folder that owns its transcript."""
    return tuple(replace(session, agent=agents.get(session.session_id, "")) for session in sessions)


def yesterday_agents(spend: Spend, now: float | None = None) -> tuple[tuple[str, float], ...]:
    """What each agent cost on the day before `now`, dearest first.

    A session counts whole, on the day of its last activity, because `session`
    carries two activity stamps and no per-day split. The day totals are not taken
    from this: they come from `daily`, which is exact, and this is the split of them
    a person can act on.
    """
    stamp = _yesterday(now)
    spent: dict[str, float] = {}
    for session in spend.sessions:
        if session.last_activity is None or session.last_activity[:10] != stamp:
            continue
        spent[session.agent] = spent.get(session.agent, 0.0) + session.cost_usd
    return tuple(sorted(spent.items(), key=lambda item: (-item[1], item[0])))


def yesterday(spend: Spend, now: float | None = None) -> DaySpend | None:
    """The day row for the day before `now`, or None when the answer has none."""
    stamp = _yesterday(now)
    return next((day for day in spend.days if day.date == stamp), None)


def check_spend(
    home: Path | None = None,
    *,
    config: Path | None = None,
    state_dir: Path | None = None,
    now: float | None = None,
) -> CheckOutcome | None:
    """One line saying what yesterday cost, per agent, and never a verdict.

    Silent when the backend is missing, failed or has no row for yesterday: a
    machine that did not talk to its bot yesterday has nothing wrong with it, and a
    permanent "could not be verified" line is one more row a person learns to skip.
    The severity is always OK, because a bill says nothing about whether the machine
    works: the owner decides what to do with the number.

    It is asked by `nexgen openclaw doctor` only. `nexgen doctor` runs on a schedule
    and starts no third-party binary, so the line is absent there by design rather
    than by a flag.
    """
    spend = read_spend(home, config=config, state_dir=state_dir, now=now)
    day = yesterday(spend, now)
    if day is None:
        return None
    return CheckOutcome(
        id=CHECK_ID,
        severity=Severity.OK,
        message=_yesterday_sentence(day, yesterday_agents(spend, now)),
    )


def render(spend: Spend) -> str:
    """The report a person reads: the days, the total, the agents, then the note."""
    if not spend.known:
        return spend.note or t("ccusage reported no OpenClaw spend for this period.")
    lines: list[str] = []
    for day in spend.days:
        models = ", ".join(day.models)
        suffix = f"  {models}" if models else ""
        lines.append(f"{day.date}  {_money(day.cost_usd):>9}  {day.tokens:>12,} tokens{suffix}")
    lines.append(f"{'total':>10}  {_money(spend.cost_usd):>9}  {spend.tokens:>12,} tokens")
    lines.append("")
    lines.append(t("By agent, each session counted on the day it was last active:"))
    for agent, tokens, cost in spend.by_agent():
        lines.append(f"  {_agent_label(agent):<12} {_money(cost):>9}  {tokens:>12,} tokens")
    if spend.note:
        lines.append("")
        lines.append(spend.note)
    return "\n".join(lines)


def as_dict(spend: Spend) -> dict:
    """One answer as plain data: what `--json` prints and what the cache keeps.

    The same shape for both, so a field the command shows and a field a later
    caller reuses cannot drift apart. `totals` and `agents` are derived here and
    ignored on the way back: the days and the sessions are the facts.
    """
    return {
        "read_at": spend.read_at,
        "since": spend.since,
        "daily": [
            {"date": day.date, "tokens": day.tokens, "cost_usd": day.cost_usd,
             "models": list(day.models)}
            for day in spend.days
        ],
        "totals": {"tokens": spend.tokens, "cost_usd": spend.cost_usd},
        "agents": [{"agent": agent or None, "tokens": tokens, "cost_usd": cost}
                   for agent, tokens, cost in spend.by_agent()],
        "sessions": [
            {"session_id": session.session_id, "agent": session.agent or None,
             "tokens": session.tokens, "cost_usd": session.cost_usd,
             "last_activity": session.last_activity}
            for session in spend.sessions
        ],
        "note": spend.note,
    }


def _filter(since: str | None) -> str | None:
    """The date filter in the form `ccusage` documents, or a `ValueError` naming it.

    Both spellings the binary accepts are accepted here, and the value is passed on
    as it was written, because the answer it buys is the answer it buys.
    """
    if since is None:
        return None
    text = str(since).strip()
    if not text:
        return None
    for form in ("%Y-%m-%d", "%Y%m%d"):
        try:
            datetime.strptime(text, form)
        except ValueError:  # nexgen: allow reason="one format not matching is the next format to try, and a value in neither form is raised below, never accepted"
            continue
        return text
    raise ValueError(f"a date looks like 2026-11-14 or 20261114, and this is {text}")


def _invoke(home: Path | None, since: str | None) -> tuple[tuple[DaySpend, ...], tuple[SessionSpend, ...], str, str]:
    """One answer: the two views, or the reason there is none at all.

    Both commands must answer. A day total with no session behind it cannot be
    attributed to an agent, and half of this report is worse than the one sentence
    that says it could not be read. The run itself is `nexgen_core.ccusage`'s: this
    function only decides that two answers are needed and what each word means.
    """
    folder = workdir(home)
    daily = ccusage.run(DAILY_COMMAND, cwd=folder, since=since)
    if daily.error:
        return (), (), daily.error, daily.detail
    sessions = ccusage.run(SESSION_COMMAND, cwd=folder, since=since)
    if sessions.error:
        return (), (), sessions.error, sessions.detail
    try:
        return parse_daily(daily.out), parse_sessions(sessions.out), "", ""
    except FormatError as exc:
        return (), (), FORMAT, str(exc)


def _day(entry, index: int) -> DaySpend:
    """One daily row, refused when a field this report prints is missing or wrong."""
    row = ccusage.object_row(entry, f"row {index}", "daily")
    stamp = ccusage.require_text(row, "date", f"row {index}")
    tokens = ccusage.require_count(row, "totalTokens", f"day {stamp}")
    cost = ccusage.require_cost(row, f"day {stamp}")
    return DaySpend(date=stamp, tokens=tokens, cost_usd=cost, models=_models(row, f"day {stamp}"))


def _session(entry, index: int) -> SessionSpend:
    """One session row, refused when a field this report prints is missing or wrong."""
    row = ccusage.object_row(entry, f"row {index}", "sessions")
    identifier = ccusage.require_text(row, "sessionId", f"row {index}")
    tokens = ccusage.require_count(row, "totalTokens", f"session {identifier}")
    cost = ccusage.require_cost(row, f"session {identifier}")
    return SessionSpend(
        session_id=identifier,
        agent="",
        tokens=tokens,
        cost_usd=cost,
        last_activity=_stamp(row, "lastActivity", f"session {identifier}"),
    )


def _models(row, label: str) -> tuple[str, ...]:
    """The models a day read, which `daily` always emits as a list of strings."""
    value = row.get("modelsUsed")
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise FormatError(f"{label} carries no modelsUsed list")
    return tuple(item.strip() for item in value if item.strip())


def _stamp(row, key: str, label: str) -> str | None:
    """An activity stamp, or None: a session the binary dates with nothing."""
    value = row.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise FormatError(f"{label} carries a {key} that is not a date")
    return value


def _time(now: float | None) -> float:
    """The moment this read happens, as an epoch the cache compares."""
    return time.time() if now is None else float(now)


def _yesterday(now: float | None) -> str:
    """The date before `now`, written the way `daily` writes a date."""
    today = datetime.fromtimestamp(_time(now)).date()
    return (today - timedelta(days=1)).isoformat()


def _store(home: Path | None, moment: float, since: str | None,
           state_dir: Path | None) -> Spend:
    """One answer bought now, attributed and kept for the next few minutes."""
    days, sessions, error, detail = _invoke(home, since)
    found = attribute(sessions, session_agents(state_dir if state_dir is not None else _state_dir(home)))
    spend = Spend(read_at=moment, days=days, sessions=found, since=since,
                  note=_note(error, detail))
    if spend.known or error:
        _write(home, spend)
    return spend


def _state_dir(home: Path | None) -> Path | None:
    """OpenClaw's own folder, read from the file that already resolves it.

    `paths.openclaw_config` owns the precedence (`$OPENCLAW_CONFIG_PATH`, then
    `$OPENCLAW_STATE_DIR/openclaw.json`, then `~/.openclaw`), so the folder holding
    the agent transcripts is its parent and no second copy of that rule is written.
    """
    from nexgen_core.paths import openclaw_config

    try:
        return Path(openclaw_config(home)).parent
    except OSError:  # nexgen: allow reason="a state folder that cannot be resolved leaves every session unattributed, and the day totals still print"
        return None


def _cached(home: Path | None, moment: float, since: str | None) -> Spend | None:
    """The stored answer while it still describes now and this filter, else None."""
    data = ccusage.cache_read(cache_path(home))
    if not ccusage.cache_is_fresh(data, moment=moment, ttl=CACHE_TTL_SECONDS):
        return None
    if (data.get("since") or None) != since:
        return None
    return Spend(
        read_at=float(data["read_at"]),
        days=_days_from(data),
        sessions=_sessions_from(data),
        since=since,
        note=str(data.get("note") or ""),
    )


def _days_from(data: dict) -> tuple[DaySpend, ...]:
    """The days a stored answer carries, skipping an entry this module cannot read."""
    return tuple(
        DaySpend(
            date=str(entry["date"]),
            tokens=int(entry["tokens"]),
            cost_usd=float(entry["cost_usd"]),
            models=tuple(str(item) for item in entry.get("models") or ()),
        )
        for entry in data.get("daily") or ()
        if isinstance(entry, dict) and {"date", "tokens", "cost_usd"} <= set(entry)
    )


def _sessions_from(data: dict) -> tuple[SessionSpend, ...]:
    """The sessions a stored answer carries, skipping an entry this module cannot read."""
    return tuple(
        SessionSpend(
            session_id=str(entry["session_id"]),
            agent=str(entry.get("agent") or ""),
            tokens=int(entry["tokens"]),
            cost_usd=float(entry["cost_usd"]),
            last_activity=None if entry.get("last_activity") is None else str(entry["last_activity"]),
        )
        for entry in data.get("sessions") or ()
        if isinstance(entry, dict) and {"session_id", "tokens", "cost_usd"} <= set(entry)
    )


def _write(home: Path | None, spend: Spend) -> None:
    """Publishes one answer in the shape `--json` prints.

    Nothing personal travels: a session id, an agent name and the numbers the view
    already carries. The store itself is `nexgen_core.ccusage`'s, shared with the
    harness archive's own cache.
    """
    ccusage.cache_write(cache_path(home), as_dict(spend))


def _note(error: str, detail: str) -> str:
    """One sentence for the report, in the language the person reads."""
    if not error:
        return ""
    if error == MISSING:
        return t("ccusage is not installed on this machine: the optional OpenClaw spend backend is skipped.")
    if error == TIMEOUT:
        return t("ccusage did not answer within {seconds} seconds: its numbers are not shown.",
                 seconds=detail)
    if error == FAILED:
        return t("ccusage exited with code {code}: its numbers are not shown.", code=detail)
    return t("ccusage output format not understood ({detail}): its numbers are not shown.",
             detail=detail)


def _yesterday_sentence(day: DaySpend, agents: tuple[tuple[str, float], ...]) -> str:
    """The one doctor line: yesterday's bill, and the agents it belongs to."""
    named = ", ".join(f"{_agent_label(agent)} {_money(cost)}" for agent, cost in agents)
    if not named:
        return t("Yesterday OpenClaw spent {cost} on {tokens} tokens.", cost=_money(day.cost_usd),
                 tokens=f"{day.tokens:,}")
    return t("Yesterday OpenClaw spent {cost} on {tokens} tokens: {agents}.",
             cost=_money(day.cost_usd), tokens=f"{day.tokens:,}", agents=named)


def _agent_label(agent: str) -> str:
    """An agent as the report names it, with the unattributed bill named as such."""
    return agent or t("(no transcript)")


def _money(value: float) -> str:
    """Dollars as a person reads them: two decimals, never a scientific number."""
    return f"${value:,.2f}"
