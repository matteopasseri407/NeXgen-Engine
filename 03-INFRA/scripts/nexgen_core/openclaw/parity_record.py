"""What one parity measurement is, where it lives, and how this machine publishes it.

One job: the shape of a record and of each lane's row, the limit a lane is judged
against, the two paths a record can be found at, and the one function that decides
where a machine may leave it. It measures nothing and judges no release: `parity.py`
runs the probes, `parity_gate.py` reads the record, and both speak in these types so
neither has to know what a JSON file looks like.

Why a file of its own. A record is read by a doctor that runs on a schedule and by a
release preflight that refuses a tag, and written by a command a person types. Three
readers of one file agree here or they disagree silently, which is the one failure
this engine keeps paying for.

Where a record may go, and why it is not always the Vault. It belongs at
`99-INDEX/OPENCLAW-PARITY.json`, because the measurement runs where the gateway runs
and the release is cut somewhere else. `updater._commit_split_pin` is the shape the
engine already uses for a Vault file: a machine allowed to write the Vault writes it
and commits it through `nexgen_core.publisher`; a machine whose role is to read the
Vault must not, and `vault_readonly.readonly_reason` is the one function that decides
that. Such a machine cannot use the vault-library either, because that server writes
Markdown notes only and refuses any other suffix, so it keeps the record in its state
directory and the caller says so, naming the file and the machine that can publish it.
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nexgen_core.files import atomic_write_text
from nexgen_core.i18n import t
from nexgen_core.paths import resolve_state_dir
from nexgen_core.vault_readonly import readonly_reason

#: The owner's rule, in input tokens per call: an agent that runs a harness inside
#: OpenClaw must cost what the same harness costs run directly, plus a light
#: overhead. The one measured lane adds 7,110 and the owner set the tolerance an
#: order of magnitude above a rounding difference, at 10,000, because past that
#: OpenClaw is not adding a harness's wrapper but a second prompt paid on every
#: call. A lane over this line fails, and a failed record blocks the release.
PARITY_OVERHEAD_TOKENS = 10_000

#: How old a record may be and still count as evidence about this release.
RECORD_MAX_AGE_DAYS = 7

#: Where the Vault keeps the record, relative to its root, and the same file under
#: the machine's state directory for a machine that may not publish the Vault.
RECORD_PARTS = ("99-INDEX", "OPENCLAW-PARITY.json")
STATE_PARTS = ("nexgen", "OPENCLAW-PARITY.json")

#: The words a lane's verdict uses. A failure is the only one that blocks anything:
#: "not applicable" is a lane nobody runs on, "skipped" is a lane with nothing to name.
PASS = "pass"
FAIL = "fail"
NOT_APPLICABLE = "not applicable"
SKIPPED = "skipped"

#: Where one record ended up after a run: the Vault and pushed, the Vault checkout
#: without the push, or this machine's state directory when it may not publish.
VAULT = "vault"
UNPUBLISHED = "vault-unpublished"
STATE = "state"

#: Commits and pushes one Vault file; the default, and what a test replaces.
Publish = Callable[[Path, Path], "tuple[int, str]"]


@dataclass(frozen=True)
class LaneResult:
    """One lane as it was measured: the two totals, their difference and the verdict."""

    lane: str
    agent: str = ""
    model: str = ""
    native_input: int | None = None
    openclaw_input: int | None = None
    verdict: str = NOT_APPLICABLE
    reason: str = ""

    @property
    def overhead(self) -> int | None:
        """What OpenClaw adds to one call, or None when a side was not measured."""
        if self.native_input is None or self.openclaw_input is None:
            return None
        return self.openclaw_input - self.native_input

    def to_dict(self) -> dict[str, Any]:
        """This lane in the shape the record stores and `--json` prints."""
        return {
            "lane": self.lane,
            "agent": self.agent,
            "model": self.model,
            "native_input": self.native_input,
            "openclaw_input": self.openclaw_input,
            "overhead": self.overhead,
            "verdict": self.verdict,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, payload: Any) -> LaneResult | None:
        """One stored lane, or None when the entry is not one this engine wrote."""
        if not isinstance(payload, Mapping):
            return None
        lane = payload.get("lane")
        verdict = payload.get("verdict")
        if not isinstance(lane, str) or not lane.strip() or not isinstance(verdict, str):
            return None
        return cls(
            lane=lane,
            agent=str(payload.get("agent") or ""),
            model=str(payload.get("model") or ""),
            native_input=count(payload.get("native_input")),
            openclaw_input=count(payload.get("openclaw_input")),
            verdict=verdict,
            reason=str(payload.get("reason") or ""),
        )


@dataclass(frozen=True)
class Record:
    """One measurement: what it ran on, when, and one row per lane."""

    engine_commit: str = ""
    openclaw_version: str = ""
    harness_versions: Mapping[str, str] = field(default_factory=dict)
    measured_at: str = ""
    lanes: tuple[LaneResult, ...] = ()

    @property
    def failed(self) -> tuple[LaneResult, ...]:
        """The lanes that failed, which is what a release refuses over."""
        return tuple(result for result in self.lanes if result.verdict == FAIL)

    @property
    def measured(self) -> tuple[LaneResult, ...]:
        """The lanes that carry two numbers, whatever their verdict."""
        return tuple(result for result in self.lanes if result.overhead is not None)

    @property
    def worst(self) -> LaneResult | None:
        """The measured lane with the largest overhead: the number a person acts on."""
        return max(self.measured, key=lambda result: result.overhead or 0, default=None)

    def age_days(self, now: float | None = None) -> float | None:
        """How old this record is, or None when it carries no stamp this code can read."""
        try:
            measured = datetime.fromisoformat(self.measured_at)
        except ValueError:  # nexgen: allow reason="a record whose stamp is not a date has no age, which the doctor reports as undated instead of hiding behind a made-up number"
            return None
        moment = time.time() if now is None else float(now)
        return (moment - measured.timestamp()) / 86400

    def to_dict(self) -> dict[str, Any]:
        """This record in the shape the Vault file and `--json` hold."""
        return {
            "engine_commit": self.engine_commit,
            "openclaw_version": self.openclaw_version,
            "harness_versions": dict(self.harness_versions),
            "measured_at": self.measured_at,
            "lanes": [result.to_dict() for result in self.lanes],
        }


@dataclass(frozen=True)
class Kept:
    """Where one record ended up, and the one sentence a person needs about it."""

    destination: str
    path: Path
    note: str = ""


def record_path(vault: Path) -> Path:
    """Where the Vault keeps the record the release gate reads."""
    return Path(vault).joinpath(*RECORD_PARTS)


def state_record_path(home: Path | None = None) -> Path:
    """The same record under the machine's state directory, for a machine that may not publish."""
    return resolve_state_dir(home).joinpath(*STATE_PARTS)


def load(paths: Sequence[Path]) -> Record | None:
    """The newest record any of these paths holds, or None when none does.

    The stamps are ISO 8601 in UTC, so the newest one is also the greatest string.
    A file that exists and cannot be read is not a record: the doctor says it has
    none and the release gate refuses, which is the safe direction for both.
    """
    found = [record for record in (_read(path) for path in paths) if record is not None]
    return max(found, key=lambda record: record.measured_at, default=None)


def _read(path: Path) -> Record | None:
    """One record as the file holds it, or None when the file is absent or not one."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):  # nexgen: allow reason="a record that cannot be read is no record: the doctor asks for a new measurement and the release gate refuses, which is the safe direction for both"
        return None
    return _record_from(payload)


def _record_from(payload: Any) -> Record | None:
    """A stored record, or None when it does not carry the fields this engine writes."""
    if not isinstance(payload, Mapping):
        return None
    lanes = payload.get("lanes")
    if not isinstance(lanes, list):
        return None
    read = [result for result in (LaneResult.from_dict(entry) for entry in lanes) if result is not None]
    versions = payload.get("harness_versions")
    return Record(
        engine_commit=str(payload.get("engine_commit") or ""),
        openclaw_version=str(payload.get("openclaw_version") or ""),
        harness_versions={str(key): str(value) for key, value in versions.items()}
        if isinstance(versions, Mapping) else {},
        measured_at=str(payload.get("measured_at") or ""),
        lanes=tuple(read),
    )


def merged(previous: Record | None, fresh: Record, order: Sequence[str] = ()) -> Record:
    """The new measurement with its own lanes replacing those of the same name.

    `--lane` measures one lane, and a record that named one lane where the last run
    named three would quietly narrow what the release gate can see. The versions and
    the commit come from the run that just happened, because that is what the record
    now describes. `order` is the lane order the caller wants listed first.
    """
    if previous is None:
        return fresh
    by_lane = {result.lane: result for result in previous.lanes}
    by_lane.update({result.lane: result for result in fresh.lanes})
    first = [lane for lane in order if lane in by_lane]
    first.extend(lane for lane in by_lane if lane not in first)
    return Record(
        engine_commit=fresh.engine_commit,
        openclaw_version=fresh.openclaw_version,
        harness_versions={**previous.harness_versions, **fresh.harness_versions},
        measured_at=fresh.measured_at,
        lanes=tuple(by_lane[lane] for lane in first),
    )


def keep(
    record: Record,
    *,
    vault: Path,
    home: Path | None = None,
    engine_root: Path | None = None,
    publish: Publish | None = None,
) -> Kept:
    """Keep the record where the gate will find it, or as near as this machine may.

    The machine-local copy is always written: it is the fallback a read-only machine
    can offer, and the doctor on this machine reads the freshest of the two.
    """
    local = state_record_path(home)
    write(local, record)
    reason = readonly_reason(Path(vault), home=home, engine_root=engine_root)
    if reason:
        return Kept(STATE, local, t(
            "This machine reads the Vault and must not publish it ({reason}), and the vault-library writes "
            "Markdown notes only, so the record stays here: copy {path} to {target} from the machine that "
            "owns the Vault, and release from there.",
            reason=reason, path=local, target=record_path(vault),
        ))
    target = record_path(vault)
    try:
        write(target, record)
    except OSError as exc:  # nexgen: allow reason="an unwritable Vault checkout leaves the machine-local copy, and the command says which file to publish by hand"
        return Kept(STATE, local, t(
            "The record could not be written to {target} ({error}); it is in {path}.",
            target=target, error=exc, path=local,
        ))
    code, message = (publish or _publish)(Path(vault), target)
    return Kept(VAULT if code == 0 else UNPUBLISHED, target, message)


def write(path: Path, record: Record) -> None:
    """Write one record as the JSON the doctor and the release gate read."""
    atomic_write_text(Path(path), json.dumps(record.to_dict(), indent=2, ensure_ascii=False) + "\n")


def _publish(vault: Path, target: Path) -> tuple[int, str]:
    """Commit and push the record with the engine's own Vault publisher."""
    from nexgen_core.publisher import Publisher

    return Publisher(Path(vault)).publish(
        message="openclaw: record what a harness costs inside OpenClaw",
        files=[Path(target).relative_to(Path(vault)).as_posix()],
    )


def stamp(moment: float) -> str:
    """The stamp a record carries: UTC, to the second, and readable by a person."""
    return datetime.fromtimestamp(moment, timezone.utc).isoformat(timespec="seconds")


def count(value: Any) -> int | None:
    """A stored token count, or None when the file carries something else there."""
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None
