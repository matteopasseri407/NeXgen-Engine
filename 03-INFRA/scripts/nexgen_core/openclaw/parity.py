"""Measure what a harness costs inside OpenClaw against what it costs run directly.

One job: for every CLI-backend lane the configured agents use, run the same short
probe message natively and through OpenClaw, compare the TOTAL input tokens of that
one model call, judge the difference against the owner's tolerance, and keep the
record. `parity_probe.py` owns the four command lines and their readers,
`parity_record.py` the shape of a record and where it may be kept, `parity_gate.py`
the doctor line and the release refusal.

The rule, and it is the owner's, not a preference: an agent that runs a harness
inside OpenClaw must cost per call what the same harness costs when run directly,
plus a light OpenClaw overhead; the tolerance is 10,000 input tokens per call and no
release may ship unless this was measured and passed.

Why the total. The first field alone would say a warm prompt is a cheap one; the
measured case for `agy` was 18,307 input tokens natively against 25,417 through
OpenClaw, and the difference of 7,110 is the overhead the owner allows. Both sides
are read as their own client reports them (`parity_probe` says which fields that is),
and both are asked in a cold session, so a resumed chat cannot make either number
look better.

What is not measured. A provider lane runs no harness, so its row says "not
applicable"; so does a lane no configured agent uses. An ACP session runs the harness
as itself, on its own prompt -- the lane `docs/squad.md` says changes the bill -- so
it is not a second prompt OpenClaw adds and it has no row here. A lane whose agent
declares no model is "skipped", with the reason.

One agent per lane, and it costs two model calls. That is why this runs on purpose,
from the command a person types, and never from a schedule: the doctor reads the
record instead.
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from nexgen_core.deploy.alignment import engine_commit, pinned_commit
from nexgen_core.i18n import t
from nexgen_core.openclaw import doctor_lanes, parity_probe as probe
from nexgen_core.openclaw.claude_cli import split_ref
from nexgen_core.openclaw.config_file import read_config
from nexgen_core.openclaw.parity_record import (
    FAIL,
    NOT_APPLICABLE,
    PARITY_OVERHEAD_TOKENS,
    PASS,
    SKIPPED,
    Kept,
    LaneResult,
    Publish,
    Record,
    keep,
    load,
    merged,
    record_path,
    stamp,
    state_record_path,
)
from nexgen_core.openclaw.terms import LOCAL_AGY_BACKEND
from nexgen_core.paths import openclaw_config, resolve_engine_root, resolve_home, resolve_vault_data

#: The message the owner measured with, kept byte for byte so a later release's
#: number is comparable with the 2026-11-15 one (18,307 against 25,417).
PROBE_MESSAGE = "Rispondi solo con la parola OK."

#: OpenClaw's runtime ids and the lane each belongs to. `codex` is upstream's own id
#: for the Codex CLI; the lane is named `codex-cli` because that is the binary it runs.
RUNTIME_LANES: dict[str, str] = {
    LOCAL_AGY_BACKEND: probe.AGY,
    "claude-cli": probe.CLAUDE,
    "codex": probe.CODEX,
    "codex-cli": probe.CODEX,
}


def lane_agents(config: Mapping[str, Any] | None) -> dict[str, doctor_lanes.LaneReading]:
    """The first agent on each supported lane, in the order the configuration lists them.

    The lane comes from `doctor_lanes`, the module that already decides which of the
    three lanes an agent's turn really runs on: this one reads its answer instead of
    parsing `openclaw.json` a second time. Only CLI-backend lanes are candidates,
    because only they put OpenClaw's own prompt in front of the harness's call.
    """
    chosen: dict[str, doctor_lanes.LaneReading] = {}
    for reading in doctor_lanes.lanes(config):
        if reading.kind != doctor_lanes.CLI:
            continue
        lane = RUNTIME_LANES.get(reading.runtime.strip().lower())
        if lane is not None:
            chosen.setdefault(lane, reading)
    return chosen


def agent_workspace(
    config: Mapping[str, Any] | None, agent_id: str, *, home: Path | None, state_dir: Path,
) -> Path:
    """The folder OpenClaw gives this agent, which is where the native call runs.

    `adopt_scan.workspace_dir` owns that precedence -- the entry's `workspace`, then
    the default with the agent id appended, then the state folder -- so the probe
    runs in the same folder the bot works in, not in whatever directory the command
    was typed from.
    """
    from nexgen_core.openclaw.adopt_scan import agent_defaults, workspace_dir

    agents = (config or {}).get("agents")
    entries = agents.get("entries") if isinstance(agents, Mapping) else None
    entry = entries.get(agent_id) if isinstance(entries, Mapping) else None
    folder = workspace_dir(Path(state_dir), resolve_home(home), str(agent_id), entry, agent_defaults(config))
    return Path(folder)


def _tail(ran: probe.Ran) -> str:
    """The last line a failed command printed, short enough for one sentence."""
    lines = [line.strip() for line in f"{ran.stderr}\n{ran.stdout}".splitlines() if line.strip()]
    return lines[-1][:200] if lines else ""


def _input_of(reader: Callable[[str], int], ran: probe.Ran, lane: str) -> tuple[int | None, str]:
    """The number a run printed, or the one sentence saying why there is none."""
    if ran.error == probe.MISSING:
        return None, t("The {lane} command is not installed here.", lane=lane)
    if ran.error == probe.TIMEOUT:
        return None, t("The {lane} command did not answer within {seconds} seconds.",
                       lane=lane, seconds=ran.detail)
    if ran.error == probe.FOLDER:
        return None, t("The folder {folder} this agent works in does not exist.", folder=ran.detail)
    if ran.returncode != 0:
        return None, t("The {lane} command exited with code {code}: {detail}",
                       lane=lane, code=ran.returncode, detail=_tail(ran))
    try:
        return reader(ran.stdout), ""
    except probe.FormatError as exc:
        return None, t("The {lane} answer was not understood ({detail}).", lane=lane, detail=exc)


def measure_lane(
    lane: str, reading: doctor_lanes.LaneReading, *, cwd: Path, session_key: str, run: probe.Runner,
) -> LaneResult:
    """One lane measured on both sides, and judged against the owner's tolerance."""
    model = split_ref(reading.model)[1]
    if not model:
        return LaneResult(
            lane=lane, agent=reading.agent, model=reading.model, verdict=SKIPPED,
            reason=t("The agent declares no model, so the two sides cannot be compared."),
        )
    native_tokens, native_problem = _input_of(
        lambda text: probe.native_input(lane, text),
        run(probe.native_argv(lane, model, PROBE_MESSAGE), cwd, probe.NATIVE_TIMEOUT_SECONDS), lane,
    )
    openclaw_tokens, openclaw_problem = _input_of(
        probe.openclaw_input,
        run(probe.openclaw_argv(reading.agent, session_key, PROBE_MESSAGE), cwd, probe.OPENCLAW_TIMEOUT_SECONDS),
        "openclaw",
    )
    problems = " ".join(part for part in (native_problem, openclaw_problem) if part)
    if problems:
        return LaneResult(
            lane=lane, agent=reading.agent, model=reading.model,
            native_input=native_tokens, openclaw_input=openclaw_tokens, verdict=FAIL, reason=problems,
        )
    overhead = (openclaw_tokens or 0) - (native_tokens or 0)
    if overhead <= PARITY_OVERHEAD_TOKENS:
        return LaneResult(lane, reading.agent, reading.model, native_tokens, openclaw_tokens, PASS)
    return LaneResult(
        lane, reading.agent, reading.model, native_tokens, openclaw_tokens, FAIL,
        t("OpenClaw adds {overhead:,} input tokens to this call, over the {limit:,} the owner allows.",
          overhead=overhead, limit=PARITY_OVERHEAD_TOKENS),
    )


def _version(run: probe.Runner, binary: str, cwd: Path) -> str:
    """One binary's version, or "" when it does not answer: evidence, never a gate."""
    ran = run(probe.version_argv(binary), cwd, probe.VERSION_TIMEOUT_SECONDS)
    return probe.version_of(ran.stdout) if not ran.error and ran.returncode == 0 else ""


def measure(
    config: Mapping[str, Any] | None,
    *,
    lanes: Sequence[str] = probe.LANES,
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
    engine_root: Path | None = None,
    run: probe.Runner | None = None,
    now: float | None = None,
) -> Record:
    """One measurement of the lanes asked for, judged, with what it ran on."""
    moment = time.time() if now is None else float(now)
    runner = run or probe.default_runner
    chosen = lane_agents(config)
    state_dir = Path(openclaw_config(home, env)).parent
    results: list[LaneResult] = []
    for lane in lanes:
        reading = chosen.get(lane)
        if reading is None:
            results.append(LaneResult(
                lane=lane, verdict=NOT_APPLICABLE, reason=t("No configured agent runs on this lane."),
            ))
            continue
        results.append(measure_lane(
            lane, reading, cwd=agent_workspace(config, reading.agent, home=home, state_dir=state_dir),
            session_key=f"parity-{lane}-{int(moment)}", run=runner,
        ))
    return Record(
        engine_commit=_engine_commit(home, engine_root),
        openclaw_version=_version(runner, "openclaw", state_dir),
        harness_versions={lane: _version(runner, probe.BINARIES[lane], state_dir) for lane in chosen},
        measured_at=stamp(moment),
        lanes=tuple(results),
    )


def _engine_commit(home: Path | None, engine_root: Path | None) -> str:
    """The commit of the engine that measured this, or the pin when there is no checkout.

    A package install has no `git rev-parse` to answer, and the record still has to say
    which engine produced it: `99-INDEX/ENGINE-PIN.txt` is the commit the Vault says the
    fleet installed, and `deploy.alignment` already reports when the two disagree.
    """
    return (engine_commit(resolve_engine_root(home, engine_root))
            or pinned_commit(resolve_vault_data(home)) or "")


def render(record: Record, kept: Kept) -> str:
    """One line per lane, then where the record went."""
    lines = [_lane_line(result) for result in record.lanes]
    counts = {word: sum(1 for result in record.lanes if result.verdict == word)
              for word in (PASS, FAIL, NOT_APPLICABLE, SKIPPED)}
    lines.append("")
    lines.append(t("{measured} measured, {failed} failed, {skipped} not measurable, of {total} lanes. "
                   "The limit is {limit:,} input tokens per call.",
                   measured=counts[PASS], failed=counts[FAIL], skipped=counts[NOT_APPLICABLE] + counts[SKIPPED],
                   total=len(record.lanes), limit=PARITY_OVERHEAD_TOKENS))
    if kept.note:
        lines.append(kept.note)
    return "\n".join(lines)


def _lane_line(result: LaneResult) -> str:
    """One lane as a person reads it: the mark, the two numbers and the difference."""
    if result.verdict in (NOT_APPLICABLE, SKIPPED):
        named = f"{result.agent} " if result.agent else ""
        return f"SKIP  {result.lane:<24} {named}{result.reason}"
    line = (f"{result.verdict.upper():<5} {result.lane:<24} {result.agent} "
            f"native {_tokens(result.native_input)}  openclaw {_tokens(result.openclaw_input)}")
    if result.overhead is not None:
        line += f"  overhead {result.overhead:+,}"
    return f"{line}  {result.reason}" if result.reason else line


def _tokens(count: int | None) -> str:
    """One side's total, or a dash for the side that answered with no number."""
    return "-" if count is None else f"{count:,}"


def as_dict(record: Record, kept: Kept) -> dict[str, Any]:
    """The same answer as data: what `--json` prints."""
    return {**record.to_dict(), "record_path": str(kept.path), "record_destination": kept.destination,
            "overhead_limit": PARITY_OVERHEAD_TOKENS}


def run_parity(
    *,
    lane: str | None = None,
    as_json: bool = False,
    dry_run: bool = False,
    home: Path | None = None,
    env: Mapping[str, str] | None = None,
    vault: Path | None = None,
    engine_root: Path | None = None,
    run: probe.Runner | None = None,
    publish: Publish | None = None,
    now: float | None = None,
    out: Callable[[str], None] = print,
) -> int:
    """Measure the lanes, keep the record, print one line per lane.

    `--dry-run` prints the commands and spends nothing: no model is called and no
    file is written. The exit code is non-zero only for a failing lane, because that
    is the one answer a release depends on.
    """
    path = openclaw_config(home, env)
    config, failure = read_config(path)
    if config is None:
        out(t("Cannot read the OpenClaw configuration at {path}{detail}.",
              path=path, detail=f": {failure}" if failure else ""))
        return 2
    wanted = probe.LANES if lane is None else (str(lane),)
    if lane is not None and lane not in probe.LANES:
        out(t("Unknown lane {lane}: this measures {lanes}.", lane=lane, lanes=", ".join(probe.LANES)))
        return 2
    moment = time.time() if now is None else float(now)
    vault_path = Path(vault) if vault is not None else resolve_vault_data(home)
    chosen = lane_agents(config)
    if dry_run:
        for line in plan(config, chosen, wanted, home=home, env=env, moment=moment, vault=vault_path):
            out(line)
        return 0
    record = merged(
        load((record_path(vault_path), state_record_path(home))),
        measure(config, lanes=wanted, home=home, env=env, engine_root=engine_root, run=run, now=moment),
        probe.LANES,
    )
    kept = keep(record, vault=vault_path, home=home, engine_root=engine_root, publish=publish)
    if as_json:
        out(json.dumps(as_dict(record, kept), indent=2, ensure_ascii=False))
    else:
        out(render(record, kept))
    return 1 if record.failed else 0


def plan(
    config: Mapping[str, Any] | None,
    chosen: Mapping[str, doctor_lanes.LaneReading],
    lanes: Sequence[str],
    *,
    home: Path | None,
    env: Mapping[str, str] | None,
    moment: float,
    vault: Path,
) -> list[str]:
    """The commands a real run would start, one line each, and nothing else.

    Written out rather than summarised, because the point of `--dry-run` is to see
    exactly which call is about to be paid for, in whose folder, on which model.
    """
    state_dir = Path(openclaw_config(home, env)).parent
    lines = [t("Dry run: {count} lane(s), nothing is called and nothing is written.", count=len(lanes))]
    for lane in lanes:
        reading = chosen.get(lane)
        if reading is None:
            lines.append(f"SKIP  {lane:<24} {t('No configured agent runs on this lane.')}")
            continue
        cwd = agent_workspace(config, reading.agent, home=home, state_dir=state_dir)
        lines.append(f"{lane:<24} {reading.agent}  {t('in {folder}', folder=cwd)}")
        lines.append("  " + " ".join(probe.native_argv(lane, split_ref(reading.model)[1], PROBE_MESSAGE)))
        lines.append("  " + " ".join(probe.openclaw_argv(
            reading.agent, f"parity-{lane}-{int(moment)}", PROBE_MESSAGE)))
    lines.append(t("The record would be written to {path}.", path=record_path(vault)))
    return lines
