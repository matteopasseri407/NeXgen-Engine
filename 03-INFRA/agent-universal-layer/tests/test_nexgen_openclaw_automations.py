"""The automation job list, and the proactive check found in it.

The engine no longer switches a heartbeat off through `agents.defaults.heartbeat`:
the pinned release schedules it as an ordinary automation job, and the shapes read
here are the documented JSON of `openclaw automations list --all --json`. These
tests pin what the reader accepts, what it refuses, and above all that a scheduler
that does not answer is never turned into an empty, green job list.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import i18n  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw import automations  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw_service import RunResult  # noqa: E402 - sys.path shim for cloned checkout


@pytest.fixture(autouse=True)
def _english():
    """The source language, whatever the machine reads: these lines are English by construction."""
    i18n.set_language("en")
    yield
    i18n.set_language(None)


def job(**overrides) -> dict:
    """One row as `automations list --all --json` prints it."""
    base = {
        "id": "job-1",
        "name": "Proactive check",
        "enabled": True,
        "schedule": {"cron": "*/30 * * * *"},
        "status": "idle",
    }
    base.update(overrides)
    return base


def test_the_provisioned_check_is_recognised_by_its_name():
    assert automations.is_heartbeat_job(job()) is True
    assert automations.is_heartbeat_job(job(name="Proactive check", schedule={"every": "30m"})) is True


def test_a_migrated_monitor_is_recognised_by_its_own_name_or_key():
    """A person edits a migrated job's name, and the engine must still find it."""
    assert automations.is_heartbeat_job(job(name="Nightly heartbeat")) is True
    assert automations.is_heartbeat_job(job(name="owner-ping", declarationKey="openclaw:heartbeat")) is True
    assert automations.is_heartbeat_job(job(name="owner-ping", displayName="Proactive check (edited)")) is True


def test_a_job_that_is_not_the_check_is_left_alone():
    assert automations.is_heartbeat_job(job(name="Context guard")) is False
    assert automations.is_heartbeat_job(job(name="Queue depth check")) is False
    assert automations.is_heartbeat_job("Proactive check") is False


def test_only_the_enabled_checks_are_the_ones_costing_model_turns():
    jobs = [
        job(),
        job(id="job-2", name="heartbeat (disabled)", enabled=False),
        job(id="job-3", name="Proactive check", enabled=False, status="disabled"),
        job(id="job-4", name="Context guard"),
    ]

    assert [row["id"] for row in automations.heartbeat_jobs(jobs)] == ["job-1", "job-2", "job-3"]
    assert [row["id"] for row in automations.enabled_heartbeat_jobs(jobs)] == ["job-1"]


def test_a_row_that_does_not_say_whether_it_is_enabled_is_taken_as_running():
    """The risk direction: a monitor nobody asked for is worse than a warning about one."""
    assert automations.is_enabled({"id": "job-1", "name": "Proactive check"}) is True


def test_the_id_and_the_name_are_what_the_cli_takes_and_what_a_person_reads():
    assert automations.job_id({"id": "job-1"}) == "job-1"
    assert automations.job_id({"jobId": "legacy-2"}) == "legacy-2"
    assert automations.job_id({}) == ""
    assert automations.job_name({"id": "job-1", "displayName": "Nightly", "name": "proactive"}) == "Nightly"
    assert automations.job_name({}) == "(unnamed job)"


def test_the_cadence_is_read_in_the_three_shapes_the_scheduler_stores():
    assert automations.schedule_text({"schedule": {"cron": "*/30 * * * *"}}) == "*/30 * * * *"
    assert automations.schedule_text({"schedule": {"every": "30m"}}) == "30m"
    assert automations.schedule_text({"schedule": {"at": "2027-02-01T16:00:00Z"}}) == "2027-02-01T16:00:00Z"
    assert automations.schedule_text({"schedule": {}}) == "(no schedule)"
    assert automations.schedule_text({}) == "(no schedule)"


def test_the_job_rows_are_found_in_both_documented_envelopes():
    rows = [job(), job(id="job-2", name="Context guard")]

    assert automations.jobs_from_payload({"ok": True, "jobs": rows}) == rows
    assert automations.jobs_from_payload(rows) == rows
    # A row that is not an object is not a job and cannot be judged as one.
    assert automations.jobs_from_payload({"jobs": [job(), "not a job"]}) == [job()]


def test_an_answer_that_is_not_a_job_list_is_unreadable_never_empty():
    """An empty list would claim 'no monitor runs here', which nothing proved."""
    assert automations.jobs_from_payload({"ok": False, "error": "gateway unreachable"}) is None
    assert automations.jobs_from_payload({"count": 3}) is None
    assert automations.jobs_from_payload(42) is None
    assert automations.jobs_from_stdout("") is None
    assert automations.jobs_from_stdout("Compacting...") is None


def test_read_jobs_asks_the_documented_command_and_reads_its_rows():
    seen: list[list[str]] = []

    def run(argv, env):
        seen.append(list(argv))
        return RunResult(0, json.dumps({"ok": True, "jobs": [job()]}), "")

    rows = automations.read_jobs(binary="openclaw", run=run)

    assert seen == [["openclaw", "automations", "list", "--all", "--json"]]
    assert [row["id"] for row in rows] == ["job-1"]


def test_read_jobs_says_it_cannot_say_when_the_scheduler_refuses():
    assert automations.read_jobs(binary="openclaw", run=lambda argv, env: RunResult(1, "", "not reachable")) is None
    assert automations.read_jobs(binary="openclaw", run=lambda argv, env: RunResult(0, "not json", "")) is None


def test_a_converged_check_is_recognised_as_light():
    """The three flags `automations edit` writes for a cheap check, and nothing else."""
    converged = job(
        sessionTarget="isolated",
        payload={"kind": "agentTurn", "lightContext": True, "toolsAllow": []},
    )

    assert automations.runs_light(converged) is True
    assert automations.runs_light(job(schedule={"every": "6h"})) is False


def test_a_check_that_keeps_the_conversation_the_files_or_the_tools_is_not_light():
    light = {"kind": "agentTurn", "lightContext": True, "toolsAllow": []}

    assert automations.runs_light(job(sessionTarget="main", payload=light)) is False
    assert automations.runs_light(job(
        sessionTarget="isolated", payload={"kind": "agentTurn", "toolsAllow": []},
    )) is False
    assert automations.runs_light(job(
        sessionTarget="isolated", payload={"kind": "agentTurn", "lightContext": True, "toolsAllow": ["*"]},
    )) is False


def test_the_cadence_is_read_from_the_intervals_the_cli_stores():
    assert automations.cadence_minutes({"schedule": {"every": "30m"}}) == 30
    assert automations.cadence_minutes({"schedule": {"every": "6h"}}) == 360
    assert automations.cadence_minutes({"schedule": {"every": "1d"}}) == 1440
    assert automations.cadence_minutes({"schedule": {"every": "90s"}}) == 2
    # A cadence nobody can read is unknown, never zero and never a warning.
    assert automations.cadence_minutes({"schedule": {"every": "every so often"}}) is None


def test_the_cadence_is_read_from_the_five_field_schedules_worth_reading():
    assert automations.cadence_minutes({"schedule": {"cron": "*/30 * * * *"}}) == 30
    assert automations.cadence_minutes({"schedule": {"cron": "0 * * * *"}}) == 60
    assert automations.cadence_minutes({"schedule": {"cron": "0 */6 * * *"}}) == 360
    assert automations.cadence_minutes({"schedule": {"cron": "0 3 * * *"}}) == 1440
    # A weekly sweep and a one-shot are not cadences this reads.
    assert automations.cadence_minutes({"schedule": {"cron": "0 3 * * 0"}}) is None
    assert automations.cadence_minutes({"schedule": {"at": "2027-02-01T16:00:00Z"}}) is None
    assert automations.cadence_minutes({}) is None


def test_the_runs_a_day_follow_from_that_cadence():
    assert automations.runs_per_day({"schedule": {"every": "30m"}}) == 48
    assert automations.runs_per_day({"schedule": {"every": "6h"}}) == 4
    assert automations.runs_per_day({"schedule": {"cron": "0 3 * * 0"}}) is None


def test_reading_the_jobs_with_detail_asks_about_the_check_and_only_about_it():
    """The list is a summary: whether a run is light lives in the payload of that job."""
    seen: list[list[str]] = []
    detail = job(
        name="Proactive check", sessionTarget="isolated",
        payload={"kind": "agentTurn", "lightContext": True, "toolsAllow": []},
    )

    def run(argv, env):
        seen.append(list(argv))
        if argv[1:4] == ["automations", "get", "job-1"]:
            return RunResult(0, json.dumps(detail), "")
        if argv[1:4] == ["automations", "get", "job-2"]:
            return RunResult(1, "", "gone")
        return RunResult(0, json.dumps({"ok": True, "jobs": [
            job(name="Proactive check"), job(id="job-2", name="Queue depth check"),
        ]}), "")

    rows = automations.read_jobs_with_detail(binary="openclaw", run=run)

    assert seen == [
        ["openclaw", "automations", "list", "--all", "--json"],
        ["openclaw", "automations", "get", "job-1", "--json"],
    ]
    # The check carries what the scheduler said about it; the other job was never asked about.
    assert rows[0]["payload"]["lightContext"] is True
    assert automations.runs_light(rows[0]) is True
    assert rows[1] == job(id="job-2", name="Queue depth check")


def test_a_detail_that_cannot_be_read_leaves_the_summary_in_place():
    """An unread job is not proven light, which is the direction the doctor must fail in."""
    def run(argv, env):
        if argv[1] == "automations" and argv[2] == "get":
            return RunResult(1, "", "not reachable")
        return RunResult(0, json.dumps({"ok": True, "jobs": [job()]}), "")

    rows = automations.read_jobs_with_detail(binary="openclaw", run=run)

    assert [row["id"] for row in rows] == ["job-1"]
    assert automations.runs_light(rows[0]) is False

