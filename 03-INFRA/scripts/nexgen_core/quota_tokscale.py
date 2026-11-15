# nexgen: one-job reason="ask tokscale for this machine's quotas and answer from its own record when it cannot be asked"
"""An optional backend: what `tokscale` asks each provider of this machine.

One job: run the optional `tokscale` binary (npm `tokscale`, MIT) and hand back
one reading per metric it prints, or one sentence saying why there is none. It is
the only reader in this report that leaves the machine: tokscale reads the logins
already stored here and asks each provider's own quota endpoint, which is how a
Claude Code plan, a Copilot plan or Antigravity gets a live percentage the engine
cannot compute from any local file. `docs/quota.md` names the boundary this
backend deliberately crosses, and the `backends:` section of `quota.yaml` turns it
off.

Nothing is half-trusted. The output carries no schema version, so a shape this
module does not recognise is refused whole and named in one line: a release that
renames a field must cost the numbers, never print the two it still understands
and guess at the third. A missing binary, a non-zero exit, a timeout and an
unreadable answer are all one line at the bottom of the report, and every other
row is printed exactly as it was before. A failure that comes after a good
answer keeps that answer's numbers beside the line, dated and marked as kept, so
one timeout does not blank every plan the engine could read a minute earlier.

One run costs seconds and asks the network, and `quota.percentage_rows` is called
by the hourly beat, by `nexgen doctor`, by the line a session prints when it
starts and by a command someone typed. So one answer is bought at most once per
`CACHE_TTL_SECONDS` and cached in the machine-local state directory, without the
output's personal fields: only the numbers this report reads travel.

A provider that throttles is not asked again on this engine's schedule. Every
number carries the moment it was really read, which for Claude is the moment
tokscale says its own sample was taken, so a reading that could not be refreshed
is printed with its true age and without a percentage instead of being dated
"now". The last good reading of a provider that stops being answered at all is
kept with it, and dropped once it is too old to describe anything. When tokscale
records the moment its own cooldown ends, this module waits for that instead of
spending another run inside it.

Claude's plan is the one reading that depends on a token another program owns:
tokscale asks Anthropic with the access token Claude Code stored, and skips the
plan whole once that token is rejected. The engine cannot mint one and does not
try: `claude auth status` makes Claude Code rewrite its own login without a model
call or a spent window, so a missing Claude is answered by one refresh and one
more run of tokscale, inside the same cache period and never in a loop. What that
command prints names the account's e-mail, so its output goes to the null device
and is never read, cached, or put in a note.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

import yaml

from nexgen_core import processes
from nexgen_core.files import atomic_write_text
from nexgen_core.i18n import t
from nexgen_core.paths import resolve_home, resolve_state_dir

#: The engine's one ISO-8601 to epoch rule, borrowed rather than copied a fourth
#: time: Codex's reader owns it, and a stamp means the same instant here.
from nexgen_core.quota_codex import _epoch as _iso_epoch

#: The executable this backend is named after, looked up on `PATH`, and the key
#: the `backends:` section of `quota.yaml` uses to name it.
BINARY = "tokscale"

#: How long one run may take before this report says it did not answer. A run
#: costs a few seconds on a network that answers; the beat runs hourly and must
#: never be held by a provider that does not.
RUN_TIMEOUT_SECONDS = 20

#: The binary whose own login refresh is the cure for a rejected Claude token.
#: tokscale reads the access token Claude Code stored, and only Claude Code
#: rewrites it, so this module asks that program rather than minting one.
LOGIN_BINARY = "claude"

#: The one subcommand that refreshes that token: it reports the login and needs
#: no model call, so it spends no window of any plan.
LOGIN_COMMAND = ("auth", "status")

#: How long that refresh may take. The same seconds as a run of tokscale, because
#: it is the same caller waiting: a command typed in, the hourly beat, a doctor.
LOGIN_TIMEOUT_SECONDS = 20

#: How long one answer is reused. Short enough that the beat sees a window that
#: has just reset, long enough that a doctor run, a `/quota`, the line a session
#: prints at its start and a command someone typed do not each pay for the same
#: run of the binary. One constant, because every one of those callers reads this
#: module's one cache: the interval is the promise, not a per-caller setting.
CACHE_TTL_SECONDS = 600

#: The longest cooldown this backend waits for, in seconds. Tokscale caps its own
#: backoff at an hour, and a record claiming more than that is not one this
#: module believes: waiting longer than that would blind the whole report to
#: every other provider to save one run.
MAX_HOLD_SECONDS = 3600

#: How long a reading may be kept after the run that bought it. A provider that
#: stops answering keeps its last number for a day, which is the longest a person
#: can read here and still know which window it describes.
REMEMBER_SECONDS = 24 * 3600

#: The provider whose endpoint imposes the cooldown `cooldown_until` reads. Its
#: record is the only one tokscale keeps, so a live deadline is always this
#: plan's, and a plan missing from the answer is this one.
THROTTLED_PROVIDER = "Claude"

#: Where tokscale records the cooldown each provider imposed on it: under its own
#: config directory, which `TOKSCALE_CONFIG_DIR` can move.
COOLDOWN_SUBPATH = ("cache", "claude-cooldowns")
CONFIG_DIR_ENV = "TOKSCALE_CONFIG_DIR"

#: The version of that record this module understands. Anything else is no
#: deadline at all: a format that changed costs one run of the interval.
COOLDOWN_VERSION = 1

#: Where the last answer waits, under the machine-local state directory.
CACHE_SUBPATH = ("nexgen", "tokscale-usage.json")

#: The section of `quota.yaml` this backend obeys, and the two values it takes.
SETTING = "backends"
AUTO = "auto"
OFF = "off"

#: The same switch for one run, so a sandbox or a scheduled job can silence the
#: backend without editing the file the owner maintains.
ENV_OVERRIDE = "NEXGEN_QUOTA_TOKSCALE"

#: Why there is no reading. Stored in the cache as a word, so the sentence a
#: person reads is written in their language at the moment they read it.
MISSING = "missing"
FAILED = "failed"
TIMEOUT = "timeout"
FORMAT = "format"
LOGIN_FAILED = "login"

#: The provider ids the engine's fallback chains already speak, by the name
#: tokscale prints. `Codex` is reached in OpenClaw as `openai` and `Claude` as
#: `anthropic`, the ids `quota._codex_rows` and `quota._claude_rows` give those
#: two lanes; the two OpenCode names are `quota.PROVIDER_NAMES`, read there.
PROVIDER_IDS = {"codex": "openai", "claude": "anthropic", "claude code": "anthropic"}


class FormatError(ValueError):
    """The output is not the shape this module was written against."""


@dataclass(frozen=True)
class Metric:
    """One window of one provider as tokscale printed it.

    `read_at` is the moment the number was really read: for a provider that
    answers now it is the run itself, and for one that could not be refreshed it
    is the moment of the sample tokscale fell back on. `stale` says that this
    sample is that fallback, so the number is real and its age is real, and a
    handover must not act on it.
    """

    provider: str
    provider_id: str
    window: str
    used_percent: float
    resets_at: float | None = None
    read_at: float | None = None
    stale: bool = False


@dataclass(frozen=True)
class TokscaleUsage:
    """Every metric one answer carried, the moment it was read, and why it may be empty."""

    read_at: float | None = None
    metrics: tuple[Metric, ...] = ()
    note: str = ""

    @property
    def known(self) -> bool:
        """Whether the backend answered with a number this report can show."""
        return bool(self.metrics)


@dataclass(frozen=True)
class _Stored:
    """One answer and the moment it was bought, as the cache file keeps it."""

    fetched_at: float
    metrics: tuple[Metric, ...] = ()
    error: str = ""
    detail: str = ""
    login: str = ""


def read_usage(home: Path | None = None, *, config: Path | None = None,
               now: float | None = None, max_age: float | None = None) -> TokscaleUsage:
    """What this machine has left, as tokscale reports it, or why it says nothing.

    `config` is the `quota.yaml` this machine declares, whose `backends` section
    can turn the backend off; `home` decides only where the cache lives, never
    which logins are read, because tokscale reads the machine's own.

    `max_age` makes this a read of the last answer only, never a run: a caller
    that must not spend a second on the network — the line a session prints when
    it starts — states how old an answer it still trusts, and gets nothing when
    the stored one is older. Its rows then carry their real moment, so the caller
    can say how old what it shows is.

    A run that answered nothing still carries the numbers an earlier run bought,
    marked as kept: the sentence says why nothing was refreshed and the rows are
    the last real reading, so one timeout does not blank every plan on the report
    for the ten minutes the failure is cached.
    """
    moment = time.time() if now is None else float(now)
    if mode(config) == OFF:
        return TokscaleUsage()
    if max_age is not None:
        stored = _stored(_payload(home), moment, lifetime=max_age)
        if stored is None or stored.error:
            return TokscaleUsage()
        return TokscaleUsage(read_at=stored.fetched_at, metrics=stored.metrics)
    hold = cooldown_until(home, now=moment)
    stored = _cached(home, moment, hold=hold, held_max_age=state_max_age(config))
    if stored is None:
        stored = _store(home, moment)
        # The run that just finished may be the one that bought the cooldown.
        hold = cooldown_until(home, now=moment)
    if stored.error:
        return TokscaleUsage(read_at=stored.fetched_at, metrics=stored.metrics,
                             note=_with_login_note(_note(stored.error, stored.detail), stored))
    return TokscaleUsage(read_at=stored.fetched_at, metrics=stored.metrics,
                         note=_with_login_note(_answer_note(stored, hold, moment), stored))


def mode(config: Path | None) -> str:
    """Whether this backend is asked at all: `auto` unless the file says `off`.

    The file is optional and so is the section, so a machine that never wrote a
    `backends:` line has the shipped behaviour. A value that is not `off` leaves
    the backend on, and the docs say so: this switch exists to silence one
    optional binary, not to spell out every way of leaving it alone.

    `NEXGEN_QUOTA_TOKSCALE=off` says the same thing for one run, without editing
    the file: a scheduler, a script, or a test that must not read the accounts of
    whoever happens to be logged in on this machine.
    """
    if os.environ.get(ENV_OVERRIDE, "").strip().casefold() == OFF:
        return OFF
    if config is None or not Path(config).is_file():
        return AUTO
    try:
        data = yaml.safe_load(Path(config).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):  # nexgen: allow reason="the same file already costs a line where its ceilings are read, and a backend nobody turned off is the shipped default"
        return AUTO
    declared = data.get(SETTING) if isinstance(data, dict) else None
    value = declared.get(BINARY) if isinstance(declared, dict) else None
    # YAML reads a bare `off` as the boolean false, so both spellings mean off.
    if value is False or (isinstance(value, str) and value.strip().casefold() == OFF):
        return OFF
    return AUTO


def state_max_age(config: Path | None = None) -> float:
    """How old a reading the owner's policy still trusts, in seconds.

    The plugin that moves a turn before it starts reads the same number from the
    same file, so this is what a reading reused during a cooldown may not exceed:
    a number older than that policy would have been refused by the reader that
    acts on it, and being kept here must not make it current again.
    `quota_policy` reads `quota`, which reads this module, so the import waits
    until the call the way the provider names do.
    """
    from nexgen_core.quota_policy import DEFAULT_STATE_MAX_AGE_SECONDS, read

    if config is None or not Path(config).is_file():
        return float(DEFAULT_STATE_MAX_AGE_SECONDS)
    policy, _unusable = read(Path(config))
    return float(policy.state_max_age_seconds)


def config_dir(home: Path | None = None) -> Path:
    """Where tokscale keeps its own state, resolved the way tokscale resolves it.

    `TOKSCALE_CONFIG_DIR` wins, then the platform default: `%APPDATA%\\tokscale`
    on Windows and `~/.config/tokscale` (or `$XDG_CONFIG_HOME/tokscale`) on the
    others, which is what the binary itself documents and does. Nothing here is
    written: this is the directory another program owns, read for one number.
    """
    override = os.environ.get(CONFIG_DIR_ENV, "").strip()
    if override:
        return Path(override)
    resolved = resolve_home(home)
    if os.name == "nt":
        appdata = os.environ.get("APPDATA") if home is None else None
        return (Path(appdata) if appdata else resolved / "AppData" / "Roaming") / BINARY
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    return (Path(xdg) if xdg else resolved / ".config") / BINARY


def cooldown_until(home: Path | None = None, *, now: float | None = None) -> float | None:
    """The moment tokscale will next ask Anthropic, or None when nothing says.

    A throttled provider answers tokscale with a wait, and tokscale records it
    once for every account it holds. `--json` carries none of it: the diagnostic
    that names the seconds goes to stderr only in the text renderer, so the
    record is the one machine-readable copy of the same deadline, and it agrees
    with that line to the second. A directory holding several records is one
    machine with several logins, and the longest wait is the one that keeps every
    account out of the endpoint.
    """
    moment = time.time() if now is None else float(now)
    folder = config_dir(home).joinpath(*COOLDOWN_SUBPATH)
    try:
        records = list(folder.glob("*.json"))
    except OSError:  # nexgen: allow reason="a state directory this module cannot list is no deadline, which is the behaviour before it read one"
        return None
    deadlines = [deadline for deadline in (_cooldown(path, moment) for path in records)
                 if deadline is not None]
    return max(deadlines) if deadlines else None


def _cooldown(path: Path, now: float) -> float | None:
    """One recorded cooldown as the moment it ends, or None when it is not one.

    The record is tokscale's own and carries no credential, only two timestamps
    and the version of the shape. Its own rules are repeated here because a
    hand-edited or moved-back clock record must cost nothing: the issuance cannot
    be in the future, the wait cannot exceed an hour, and the record is ignored
    whole when the version is not the one this module knows.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # nexgen: allow reason="a record that cannot be read is no deadline: the run happens on the usual interval, exactly as before this file existed"
        return None
    if not isinstance(data, dict) or data.get("version") != COOLDOWN_VERSION:
        return None
    issued, expires = data.get("issued_at"), data.get("expires_at")
    if not _number(issued) or not _number(expires):
        return None
    if issued > now or expires <= now or expires - issued > MAX_HOLD_SECONDS:
        return None
    return float(expires)


def _number(value) -> bool:
    """Whether a value is a timestamp this module can compare with a moment."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def provider_id(name: str) -> str:
    """The id a fallback chain speaks, for the provider name tokscale printed.

    The two OpenCode lanes are named in `quota.PROVIDER_NAMES`, which cannot be
    imported at module level: `quota` reads this module. A provider this engine
    has never read locally keeps a slug of its own name, so a chain that names it
    still matches and no window borrows another plan's id.
    """
    from nexgen_core.quota import PROVIDER_NAMES

    key = name.strip().casefold()
    known = {display.casefold(): provider for provider, display in PROVIDER_NAMES.items()}
    known.update(PROVIDER_IDS)
    if key in known:
        return known[key]
    return "-".join("".join(char if char.isalnum() else " " for char in key).split()) or key


def report_rows(usage: TokscaleUsage, local: list, notes: list[str]) -> list:
    """The rows the report shows: the live ones, and the local rows they did not answer for.

    One entry point, because the two decisions belong together: a plan the
    backend reported is shown once, from its live number, and the local reader of
    that same plan stays the answer for a run where the backend has nothing, so
    nothing is ever printed twice and nothing is silently lost.

    `QuotaRow` and its alert band are the report's own vocabulary and live in
    `quota`, which imports this module: the import waits until the call, the way
    the failover policy reaches the same file through `quota`. A plan is matched
    by the provider id a chain speaks, and by its name when the local row carries
    no id (Antigravity): the same plan, written by two readers.

    A reading that could not be refreshed keeps its number and its own moment,
    and loses the percentage: `quota.STATE_CACHED` is the state that says so, and
    it is the same rule as a stale local reading, so no handover acts on a window
    whose last measurement belongs to a period that may already be over.
    """
    from nexgen_core.quota import STATE_CACHED, QuotaRow, _state

    if usage.note:
        notes.append(usage.note)
    if not usage.known:
        return list(local)
    answered = {metric.provider_id for metric in usage.metrics if metric.provider_id}
    named = {metric.provider.strip().casefold() for metric in usage.metrics}
    kept = [row for row in local
            if row.provider_id not in answered and row.provider.strip().casefold() not in named]
    return kept + [
        QuotaRow(
            provider=metric.provider,
            provider_id=metric.provider_id,
            window=metric.window,
            used=metric.used_percent,
            unit="percent",
            percent=None if metric.stale else metric.used_percent,
            resets_at=metric.resets_at,
            read_at=metric.read_at if metric.read_at is not None else usage.read_at,
            state=STATE_CACHED if metric.stale else _state(metric.used_percent),
        )
        for metric in usage.metrics
    ]


def cache_path(home: Path | None = None) -> Path:
    """Where the last answer waits: machine-local state, never the engine checkout."""
    return resolve_state_dir(home).joinpath(*CACHE_SUBPATH)


def _binary() -> str | None:
    """The executable `tokscale` on `PATH`, or None: this backend is optional on purpose."""
    return shutil.which(BINARY)


def _login_binary() -> str | None:
    """The executable `claude` on `PATH`, or None: a machine without it has no login to refresh."""
    return shutil.which(LOGIN_BINARY)


def workdir(home: Path | None = None) -> Path:
    """The folder a run executes in: the machine's state dir, never the caller's directory.

    tokscale keeps its own settings in a `.tokscale/` folder in the directory it
    is started from, and the engine calls it from wherever a command, the hourly
    beat or the line a session prints happens to be. Left alone, that folder
    lands in the repository a person is working in, so every run starts in the
    state directory instead: it is machine-local, it is writable, and it is
    where this backend's own cache already lives.
    """
    folder = cache_path(home).parent
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:  # nexgen: allow reason="an unwritable state dir cannot be the cwd, and the temp dir is the one folder every host can start a program in"
        return Path(tempfile.gettempdir())
    return folder


def _invoke(home: Path | None = None) -> tuple[tuple[Metric, ...], str, str]:
    """One run of the binary: its metrics, or the reason there are none."""
    executable = _binary()
    if executable is None:
        return (), MISSING, ""
    try:
        code, text = _spawn(executable, workdir(home))
    except subprocess.TimeoutExpired:
        return (), TIMEOUT, str(RUN_TIMEOUT_SECONDS)
    except (OSError, processes.ShimEntryError):  # nexgen: allow reason="a binary that cannot be started, and an npm shim whose node entry is gone, are the missing one as far as this report is concerned: it is said in one line"
        return (), MISSING, ""
    if code != 0:
        return (), FAILED, str(code)
    return _parse(text)


def _answer(home: Path | None = None, *,
            refresh_login: bool = True) -> tuple[tuple[Metric, ...], str, str, str]:
    """One answer, and the reason Claude's rejected login could not be refreshed.

    `tokscale usage --json` omits a provider it has no sample for, so a rejected
    Claude token is the plan's absence from a good answer rather than an error
    inside it. Only Claude Code can rewrite that token, so one `claude auth
    status` runs and the binary is asked exactly once more; this function is
    called at most once per cache period, which is what keeps the refresh at one
    per period and out of any loop. `refresh_login` is false while tokscale has
    recorded a cooldown, because a throttled provider is not re-asked on this
    engine's schedule and its token is not the reason it is missing.
    """
    metrics, error, detail = _invoke(home)
    executable = _login_binary()
    if error or _carries_claude(metrics) or not refresh_login or executable is None:
        return metrics, error, detail, ""
    if _refresh_login(executable, home):
        again, error, detail = _invoke(home)
        return again, error, detail, ""
    return metrics, error, detail, LOGIN_FAILED


def _carries_claude(metrics: tuple[Metric, ...]) -> bool:
    """Whether this answer already holds the plan a refreshed login would bring back."""
    return any(metric.provider.strip().casefold() == THROTTLED_PROVIDER.casefold()
               for metric in metrics)


def _start_login(executable: str, cwd: Path) -> subprocess.Popen:
    """`claude auth status` with no window, no inherited stdin, and nowhere for its output.

    Its standard output and standard error are the null device rather than a pipe
    or a file: the answer names the account's e-mail, and nothing this module
    keeps may carry it, so there is nothing to read and nothing to recover later.
    """
    return subprocess.Popen(
        processes.windows_command_argv([executable, *LOGIN_COMMAND]),
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        cwd=str(cwd), **processes.no_window_kwargs(),
    )


def _refresh_login(executable: str, home: Path | None = None) -> bool:
    """Whether `claude auth status` renewed the stored token, spending no window.

    Claude Code rewrites its own credentials file when it is asked for its auth
    status, which is what lets the next run of tokscale ask Anthropic again. A
    binary that cannot start, exits non-zero or outlives the deadline is a login
    that was not refreshed: the report keeps saying Claude is unknown and adds
    the one sentence `_login_note` writes.
    """
    try:
        proc = _start_login(executable, workdir(home))
        proc.communicate(timeout=LOGIN_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:  # nexgen: allow reason="a refresh that outlived its deadline is stopped with the engine's own tree cleanup and reported as a login that was not refreshed"
        processes.force_stop_process_tree(proc)
        return False
    except (OSError, processes.ShimEntryError):  # nexgen: allow reason="a refresh that cannot be started is a login that was not refreshed, which the report says in one sentence: the account behind it is never named"
        return False
    return proc.returncode == 0


def _spawn(executable: str, cwd: Path) -> tuple[int, str]:
    """Run `tokscale usage --json` with no console window and no inherited stdin.

    The npm shim on Windows is a batch file, so the argv comes from the engine's
    own resolver rather than from a guess about what `PATH` holds. `cwd` is the
    state directory, which `workdir` decides, so the `.tokscale/` folder this
    binary creates never appears in the directory the caller was working in.

    The child is owned rather than handed to `subprocess.run`, because killing
    the child alone is not enough to end the run: the shim starts `node` as a
    grandchild, that grandchild inherits the pipe, and after the deadline the
    read waited for an EOF only a surviving `node` could close. `communicate`
    stops at the timeout and `force_stop_process_tree` is the engine's own
    bounded cleanup, so a binary that hangs costs one timeout instead of the
    command that asked.
    """
    argv = processes.windows_command_argv([executable, "usage", "--json"])
    proc = subprocess.Popen(
        argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        encoding="utf-8", errors="replace", cwd=str(cwd), **processes.no_window_kwargs(),
    )
    group = proc.pid if os.name == "posix" else None
    answered = False
    try:
        out, _errors = proc.communicate(timeout=RUN_TIMEOUT_SECONDS)
        answered = True
    finally:
        if not answered:
            processes.force_stop_process_tree(proc, process_group=group)
    return proc.returncode, out


def _parse(text: str) -> tuple[tuple[Metric, ...], str, str]:
    """What one answer carries, or `format` and the first thing this module cannot read."""
    try:
        payload = json.loads(text)
    except ValueError:
        return (), FORMAT, "not JSON"
    try:
        return _metrics(payload), "", ""
    except FormatError as exc:
        return (), FORMAT, str(exc)


def _metrics(payload) -> tuple[Metric, ...]:
    """Every metric in one answer, or a `FormatError` naming the first thing missing."""
    if not isinstance(payload, list):
        raise FormatError("the top level is not a list")
    found: list[Metric] = []
    for entry in payload:
        if not isinstance(entry, dict):
            raise FormatError("a provider entry is not an object")
        name = entry.get("provider")
        declared = entry.get("metrics")
        if not isinstance(name, str) or not name.strip():
            raise FormatError("a provider entry has no name")
        if not isinstance(declared, list):
            raise FormatError(f"{name} carries no metrics list")
        read_at, stale = _freshness(name.strip(), entry.get("freshness"))
        found.extend(_metric(name.strip(), item, read_at, stale) for item in declared)
    return tuple(found)


def _freshness(provider: str, declared) -> tuple[float | None, bool]:
    """When the numbers beside it were read, and whether that is a fallback sample.

    Only some providers carry it, and the one this engine needs it for is
    Claude: a throttled plan is answered from tokscale's own last sample with
    `stale` set, and the moment inside is when that sample was taken rather than
    when this run happened. That difference is the whole point of reading it, so
    a `freshness` this module cannot read refuses the answer instead of dating an
    old number as a new one.
    """
    if declared is None:
        return None, False
    if not isinstance(declared, dict) or not _number(declared.get("fetched_at")):
        raise FormatError(f"{provider} carries a freshness this engine cannot read")
    return float(declared["fetched_at"]), bool(declared.get("stale"))


def _metric(provider: str, item, read_at: float | None, stale: bool) -> Metric:
    """One metric, refused when a field this report reads is missing or of the wrong type."""
    if not isinstance(item, dict):
        raise FormatError(f"{provider} has a metric that is not an object")
    label = item.get("label")
    used = item.get("used_percent")
    if not isinstance(label, str) or not label.strip():
        raise FormatError(f"{provider} has a window with no label")
    if "resets_at" not in item:
        raise FormatError(f"{provider}/{label} carries no resets_at")
    if not isinstance(used, (int, float)) or isinstance(used, bool):
        raise FormatError(f"{provider}/{label} carries no numeric used_percent")
    resets = item["resets_at"]
    if resets is not None and not isinstance(resets, str):
        raise FormatError(f"{provider}/{label} carries a resets_at that is not a date")
    return Metric(
        provider=provider,
        provider_id=provider_id(provider),
        window=label.strip(),
        used_percent=float(used),
        resets_at=_epoch(provider, label, resets),
        read_at=read_at,
        stale=stale,
    )


def _epoch(provider: str, label: str, stamp: str | None) -> float | None:
    """A reset as the standard library reads it: `Z`, an offset, or a date alone.

    A stamp that is a string and still cannot be read is a format this module does
    not understand, not a window without a reset: the answer is refused whole.
    """
    if stamp is None:
        return None
    moment = _iso_epoch(stamp)
    if moment is None:
        raise FormatError(f"{provider}/{label} carries a resets_at this engine cannot read")
    return moment


def _store(home: Path | None, moment: float) -> _Stored:
    """One answer kept for the next few minutes, whatever it said.

    The reading this run did not answer for is added back from the last one, so a
    provider that stops answering keeps the number it last gave instead of
    vanishing from the report. An answer with no numbers and no failure is nothing
    to remember: it costs one cheap run more, and a file that says "nothing" is a
    file the next reader has to reason about for no gain. A login that could not be
    refreshed is the exception, because keeping it is what stops the next caller
    from asking Claude Code again inside the same ten minutes.
    """
    hold = cooldown_until(home, now=moment)
    metrics, error, detail, login = _answer(home, refresh_login=hold is None)
    stamped = tuple(metric if metric.read_at is not None else replace(metric, read_at=moment)
                    for metric in metrics)
    stored = _Stored(fetched_at=moment,
                     metrics=stamped + _carried(_payload(home), stamped, moment),
                     error=error, detail=detail, login=login)
    if stored.metrics or error or login:
        _write(home, stored)
    return stored


def _carried(data, answered: tuple[Metric, ...], moment: float) -> tuple[Metric, ...]:
    """The readings of the last run that this one did not answer for, marked as kept.

    A provider's own last number is worth more than the line that says there is
    none, as long as its age travels with it: a throttled Claude plan keeps its
    last window in the report instead of disappearing from it. A reading too old
    to describe anything is dropped, so a plan unplugged for a day leaves the
    report instead of lingering as a number nobody can date.
    """
    named = {metric.provider for metric in answered}
    return tuple(
        replace(metric, stale=True)
        for metric in _metrics_from(data)
        if metric.provider not in named
        and metric.read_at is not None
        and 0 <= moment - metric.read_at <= REMEMBER_SECONDS
    )


def _payload(home: Path | None) -> dict | None:
    """The cache file as the object it holds, or None when it is not one."""
    try:
        data = json.loads(cache_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):  # nexgen: allow reason="an unreadable cache is a cache miss: buying the answer again is exactly what this module would do without a cache"
        return None
    return data if isinstance(data, dict) else None


def _cached(home: Path | None, moment: float, *, hold: float | None = None,
            held_max_age: float = CACHE_TTL_SECONDS) -> _Stored | None:
    """The stored answer while it still describes now, else None.

    Two lifetimes, and the shorter one is the promise: an answer is reused for
    the interval every caller shares. The longer one is tokscale's own cooldown,
    during which a fresh run could only ask a throttled provider again, so the
    answer is reused instead — every row keeps its number and the age it carries,
    and the note says how old the answer is. That second lifetime is the age the
    owner's policy trusts (`state_max_age`), never a number of this module's own:
    a reading older than what the plugin would have refused must be bought again,
    and comes back as the kept reading it is, without a percentage.
    """
    data = _payload(home)
    fresh = _stored(data, moment)
    if fresh is not None:
        return fresh
    if hold is None or moment >= hold:
        return None
    held = _stored(data, moment, lifetime=held_max_age)
    return held if held is not None and held.metrics else None


def _stored(data, moment: float, *, lifetime: float = CACHE_TTL_SECONDS) -> _Stored | None:
    """The cache file as one answer, or None when it is stale or not this module's."""
    if not isinstance(data, dict):
        return None
    fetched_at = data.get("fetched_at")
    if not _number(fetched_at):
        return None
    if not 0 <= moment - float(fetched_at) < lifetime:
        return None
    return _Stored(
        fetched_at=float(fetched_at),
        metrics=_metrics_from(data),
        error=str(data.get("error") or ""),
        detail=str(data.get("detail") or ""),
        login=str(data.get("login") or ""),
    )


def _metrics_from(data) -> tuple[Metric, ...]:
    """The metrics a stored answer carries, skipping an entry this module cannot read."""
    if not isinstance(data, dict):
        return ()
    return tuple(
        Metric(
            provider=str(entry["provider"]),
            provider_id=str(entry["provider_id"]),
            window=str(entry["window"]),
            used_percent=float(entry["used_percent"]),
            resets_at=None if entry.get("resets_at") is None else float(entry["resets_at"]),
            read_at=None if entry.get("read_at") is None else float(entry["read_at"]),
            stale=bool(entry.get("stale")),
        )
        for entry in data.get("metrics") or ()
        if isinstance(entry, dict) and {"provider", "provider_id", "window", "used_percent"} <= set(entry)
    )


def _write(home: Path | None, stored: _Stored) -> None:
    """Publishes one answer, without the personal fields the output also carries.

    A state directory that cannot be written costs the next call one more run of
    the binary, never the report being printed now.
    """
    payload = {
        "fetched_at": stored.fetched_at,
        "error": stored.error,
        "detail": stored.detail,
        "login": stored.login,
        "metrics": [
            {"provider": metric.provider, "provider_id": metric.provider_id,
             "window": metric.window, "used_percent": metric.used_percent,
             "resets_at": metric.resets_at, "read_at": metric.read_at,
             "stale": metric.stale}
            for metric in stored.metrics
        ],
    }
    try:
        atomic_write_text(cache_path(home), json.dumps(payload, indent=2, sort_keys=True) + "\n")
    except OSError:  # nexgen: allow reason="a cache that cannot be written costs a second run, not the report the person is waiting for"
        return


def _answer_note(stored: _Stored, hold: float | None, moment: float) -> str:
    """The one line the report adds under these numbers, or nothing at all."""
    return _stale_note(stored, hold, moment) or _held_note(stored, hold, moment)


def _with_login_note(note: str, stored: _Stored) -> str:
    """This answer's note, with the sentence a login that stayed stale deserves."""
    return " ".join(part for part in (note, _login_note(stored)) if part)


def _login_note(stored: _Stored) -> str:
    """Why Claude is still missing after its login was refreshed, without the account.

    One sentence a person can act on: the token tokscale holds was rejected and
    asking Claude Code did not renew it, so the plan's own windows are not shown
    and the local token counts are all that remains. It names no e-mail and no
    account id, because that command's answer carries both and nothing here keeps
    either.
    """
    if stored.login != LOGIN_FAILED or _carries_claude(stored.metrics):
        return ""
    return t("Claude's stored access token was rejected and its login could not be refreshed: "
             "the plan's own windows are not shown.")


def _stale_note(stored: _Stored, hold: float | None, moment: float) -> str:
    """One sentence per age when a provider could not be refreshed, and that age.

    The providers are grouped by how old their own reading is, so a plan whose
    sample is forty minutes old is never described with the age of another one's
    four hours: each sentence dates the readings it names, and a machine where
    every kept reading is equally old still prints one line.
    """
    by_age: dict[str, list[str]] = {}
    for metric in stored.metrics:
        if not metric.stale:
            continue
        read_at = metric.read_at if metric.read_at is not None else stored.fetched_at
        named = by_age.setdefault(_age_text(moment - read_at), [])
        if metric.provider not in named:
            named.append(metric.provider)
    if not by_age:
        return ""
    return " ".join(
        _kept_sentence(sorted(providers), age, hold, moment)
        for age, providers in by_age.items()
    )


def _kept_sentence(providers: list[str], age: str, hold: float | None, moment: float) -> str:
    """The one line for the providers whose last reading is `age` old."""
    named = ", ".join(providers)
    if hold is None:
        return t("tokscale could not refresh {providers}: showing the reading from {age} ago for it.",
                 providers=named, age=age)
    return t("tokscale could not refresh {providers}, and is rate-limited until {when} ({wait} from now): showing the reading from {age} ago for it.",
             providers=named, when=_clock(hold), wait=_age_text(hold - moment), age=age)


def _held_note(stored: _Stored, hold: float | None, moment: float) -> str:
    """Why nothing was refreshed at all, while a cooldown this backend honours runs.

    A throttled provider with no sample left in tokscale is not mentioned in its
    output at all, so without this line the report would show Claude Code's
    tokens and never say that the plan's own windows exist and could not be
    asked for. When that plan is in the answer, what is left to say is how old
    the numbers on screen are, which no other line states.
    """
    if hold is None:
        return ""
    if not any(metric.provider.strip().casefold() == THROTTLED_PROVIDER.casefold()
               for metric in stored.metrics):
        return t("{provider}: tokscale is rate-limited until {when} ({wait} from now), so the plan's own windows are not shown.",
                 provider=THROTTLED_PROVIDER, when=_clock(hold), wait=_age_text(hold - moment))
    return t("tokscale is rate-limited until {when} ({wait} from now): these readings are from {age} ago.",
             when=_clock(hold), wait=_age_text(hold - moment),
             age=_age_text(moment - stored.fetched_at))


def _clock(moment: float) -> str:
    """A moment as the hour and minute a person reads: 22:41."""
    return datetime.fromtimestamp(moment).strftime("%H:%M")


def _age_text(seconds: float) -> str:
    """A duration as the shortest form that is still exact enough: 4m, 3h 12m.

    `quota.age_text` is the report's one renderer and lives in the module that
    imports this one, so it is reached at the call the way the provider names are.
    """
    from nexgen_core.quota import age_text

    return age_text(seconds)


def _note(error: str, detail: str) -> str:
    """One sentence for the report, in the language the person reads."""
    if error == MISSING:
        return t("tokscale is not installed on this machine: the optional quota backend is skipped.")
    if error == TIMEOUT:
        return t("tokscale did not answer within {seconds} seconds: its numbers are not shown.",
                 seconds=detail)
    if error == FAILED:
        return t("tokscale exited with code {code}: its numbers are not shown.", code=detail)
    return t("tokscale output format not understood ({detail}): its numbers are not shown.",
             detail=detail)
