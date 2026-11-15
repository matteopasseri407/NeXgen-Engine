# nexgen: one-job reason="run the optional ccusage binary once, and keep one answer per view, so each adapter owns only its own reading"
"""One run of the optional `ccusage` binary, and one cached answer per view.

One job: look `ccusage` up on `PATH`, run one command under a deadline with no
console window and no inherited stdin, and hand the caller either its standard
output or the one word saying why there is none. It also stores and reloads one
answer per view in the machine-local state directory, because a run walks a
database of transcripts and costs seconds.

Why a shared module. Two adapters already read this binary: `openclaw/spend.py`
answers what the bot spent, and `nexgen_local/supervision_spend.py` answers what
the harness archive spent, using ccusage's fork deduplication. Both need the same
three awkward details, and a second copy of them is how two numbers drift apart:
the npm shim on Windows is a batch file, so the argv comes from the engine's own
resolver; the shim's `node` grandchild keeps the pipe after the shim is killed,
so the child is owned and its tree is stopped on a timeout; and a missing binary
is an answer, never an exception.

What stays with the adapter. Which command it runs, what a row means, the
sentences a person reads and whether an answer is reused are the adapter's
decisions, so another view is another caller here and never another flag. This
module writes no number into a note: it hands back a word and a detail, and the
caller writes its own sentence in the language the person reads.

The one switch is here too. `backends: {ccusage: off}` in `quota.yaml` names this
binary, so every adapter reading it obeys the same line: a machine that silenced
it for the bot's own spend did not ask for a second reader of local transcripts.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml

from nexgen_core import processes
from nexgen_core.files import atomic_write_text

#: The executable this module is named after, looked up on `PATH`.
BINARY = "ccusage"

#: How long one run may take before the caller is told it did not answer. A run
#: walks local transcripts and opens each store read-only, which is seconds on a
#: large archive and never a network call; the deadline is generous enough for
#: that walk and short enough that a hung run costs no command.
RUN_TIMEOUT_SECONDS = 30

#: How long one answer is reused: a command and the doctor asked inside the same
#: ten minutes pay for one run between them, and nothing here runs faster.
CACHE_TTL_SECONDS = 600

#: Why there is no reading. A word rather than a sentence, so the caller writes
#: the sentence in the reader's language at the moment it is read.
MISSING = "missing"
FAILED = "failed"
TIMEOUT = "timeout"
FORMAT = "format"

#: The section of `quota.yaml` that names this binary, and the one value that
#: switches it off. One binary, one switch: every adapter reading it obeys it.
SETTING = "backends"
OFF = "off"


class FormatError(ValueError):
    """The output is not the shape this module was written against."""


@dataclass(frozen=True)
class Answer:
    """What one run produced: its output, or the word saying why it printed none."""

    out: str = ""
    error: str = ""
    detail: str = ""

    @property
    def ok(self) -> bool:
        """Whether the binary ran and exited zero."""
        return not self.error


def binary() -> str | None:
    """The executable `ccusage` on `PATH`, or None: this backend is optional on purpose."""
    return shutil.which(BINARY)


def enabled(config: Path | None) -> bool:
    """Whether `quota.yaml` lets this binary be asked at all: on unless it says `off`.

    The file and the section are both optional, so a machine that never wrote a
    `backends:` line has the shipped behaviour. YAML reads a bare `off` as the
    boolean false, so both spellings mean off. A file that cannot be parsed turns
    nothing off, because a broken quota file is a separate complaint.
    """
    if config is None or not Path(config).is_file():
        return True
    try:
        data = yaml.safe_load(Path(config).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):  # nexgen: allow reason="a quota file that cannot be read turns nothing off, which is the shipped behaviour"
        return True
    declared = data.get(SETTING) if isinstance(data, dict) else None
    value = declared.get(BINARY) if isinstance(declared, dict) else None
    return not (value is False or (isinstance(value, str) and value.strip().casefold() == OFF))


def run(command: tuple[str, ...], *, cwd: Path,
        since: str | None = None, until: str | None = None) -> Answer:
    """One run of `command`, with the date filters the binary documents.

    `since` and `until` are the spellings `ccusage` accepts (`YYYY-MM-DD` or
    `YYYYMMDD`); the caller validates them, because what a bad date costs is the
    caller's answer. `cwd` is mandatory: the binary resolves a relative data
    directory against the working directory, and starting it wherever a command
    happens to be typed is how a read lands in the wrong folder.

    A run that cannot start, exits non-zero or outlives `RUN_TIMEOUT_SECONDS` is
    an `Answer` carrying `MISSING`, `FAILED` or `TIMEOUT` with the detail worth
    printing (the exit code or the deadline), never an exception.
    """
    executable = binary()
    if executable is None:
        return Answer(error=MISSING)
    argv = [executable, *command]
    if since:
        argv.extend(["--since", since])
    if until:
        argv.extend(["--until", until])
    try:
        code, text = spawn(argv, cwd)
    except subprocess.TimeoutExpired:
        return Answer(error=TIMEOUT, detail=str(RUN_TIMEOUT_SECONDS))
    except (OSError, processes.ShimEntryError):  # nexgen: allow reason="a binary that cannot be started, and an npm shim whose node entry is gone, are the missing one as far as a reading is concerned: the caller says so in one line"
        return Answer(error=MISSING)
    if code != 0:
        return Answer(error=FAILED, detail=str(code))
    return Answer(out=text)


def spawn(argv: list[str], cwd: Path) -> tuple[int, str]:
    """Run one command to completion, returning its exit code and its standard output.

    The child is owned rather than handed to `subprocess.run`, because killing the
    npm shim alone leaves the `node` grandchild holding the pipe open; the
    engine's bounded tree cleanup ends both. Standard error is discarded: the
    binary's diagnostics are not what a caller reads, and a failed run is already
    one sentence.
    """
    proc = subprocess.Popen(
        processes.windows_command_argv(argv), stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, encoding="utf-8",
        errors="replace", cwd=str(cwd), **processes.no_window_kwargs(),
    )
    answered = False
    try:
        out, _errors = proc.communicate(timeout=RUN_TIMEOUT_SECONDS)
        answered = True
    finally:
        if not answered:
            processes.force_stop_process_tree(proc)
    return proc.returncode, out


def document(text: str, key: str) -> list:
    """The rows under `key`, or a `FormatError` naming what the document is missing.

    Every view these adapters read prints its rows beside a `totals` object. Both
    are required although each adapter recomputes its own total from the rows: the
    two keys are emitted together, so a document carrying one and not the other is
    a document whose shape moved, and the next field to move would be one of the
    numbers a person is shown.
    """
    try:
        payload = json.loads(text)
    except ValueError:
        raise FormatError("not JSON") from None
    if not isinstance(payload, dict):
        raise FormatError("the top level is not an object")
    rows = payload.get(key)
    if not isinstance(rows, list):
        raise FormatError(f"there is no {key} list")
    if not isinstance(payload.get("totals"), dict):
        raise FormatError("there is no totals object")
    return rows


def object_row(entry, label: str, view: str) -> dict:
    """One row as an object, or a `FormatError` naming the view it came from."""
    if not isinstance(entry, dict):
        raise FormatError(f"{view}: {label} is not an object")
    return entry


def require_text(row: dict, key: str, label: str) -> str:
    """A field that must be a non-empty string, such as a date or a session id."""
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise FormatError(f"{label} carries no {key}")
    return value.strip()


def require_count(row: dict, key: str, label: str) -> int:
    """A token count that must be a whole number, never a boolean or a float."""
    value = row.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise FormatError(f"{label} carries no numeric {key}")
    return value


def require_cost(row: dict, label: str) -> float:
    """The dollars of one row, from the `totalCost` the binary printed."""
    value = row.get("totalCost")
    if not is_number(value) or value < 0:
        raise FormatError(f"{label} carries no numeric totalCost")
    return float(value)


def is_number(value) -> bool:
    """Whether a value is a number a caller can print."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def cache_read(path: Path) -> dict | None:
    """The answer stored at `path` as the object it holds, or None when it is not one."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # nexgen: allow reason="an unreadable cache is a cache miss: buying the answer again is what the caller would do without a cache"
        return None
    return data if isinstance(data, dict) else None


def cache_is_fresh(payload: dict | None, *, moment: float, ttl: float = CACHE_TTL_SECONDS) -> bool:
    """Whether a stored answer was bought inside the last `ttl` seconds.

    A `read_at` in the future is stale rather than fresh: a clock that moved
    backwards must buy a reading, never make an old one look new.
    """
    if not isinstance(payload, dict):
        return False
    fetched_at = payload.get("read_at")
    if not is_number(fetched_at):
        return False
    return 0 <= moment - float(fetched_at) < ttl


def cache_write(path: Path, payload: dict) -> None:
    """Publish one answer where the next caller reads it, or lose the cache write.

    A state directory that cannot be written costs the next caller one more run of
    the binary, never the report a person is waiting for.
    """
    try:
        atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    except OSError:  # nexgen: allow reason="a cache that cannot be written costs a second run, not the report the person is waiting for"
        return
