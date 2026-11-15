"""The `openclaw.groom` line: a guard that runs is green, one that stopped is red, and every
chat nothing can compact is named.

The line is judged on two read-only facts, so no test here starts a gateway: the job
row the scheduler prints, and the record the last run wrote. What the policy picks
is `test_nexgen_openclaw_sessions_groom.py`'s question.
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
from nexgen_core.openclaw import groom_memory, groom_run  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.doctor import collect  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.doctor_groom import CHECK_ID, check_groom  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.groom_policy import IDLE_MIN_TOKENS  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.report import Severity  # noqa: E402 - sys.path shim for cloned checkout

NOW = 1_800_000_000_000
HOUR_MS = 60 * 60 * 1000


@pytest.fixture(autouse=True)
def _english():
    """The source language, whatever the machine reads: these lines are English by construction."""
    i18n.set_language("en")
    yield
    i18n.set_language(None)


def guard_job(**extra) -> dict:
    """The hourly job `setup` declares, as `automations list --all --json` prints it."""
    job = {
        "id": "job-guard",
        "name": "nexgen-context-guard",
        "displayName": "Context guard",
        "declarationKey": "nexgen:context-guard",
        "enabled": True,
        "schedule": {"cron": "0 * * * *"},
        "lastRunAtMs": NOW - HOUR_MS,
        "lastRunStatus": "ok",
        "status": "ok",
    }
    job.update(extra)
    return job


def saved_run(home: Path, **fields) -> dict:
    """The record a real run writes, with only the fields a test cares about changed."""
    recorded = {
        "schema_version": 1,
        "at": NOW - HOUR_MS,
        "cap": 1,
        "waiting": 0,
        "compacted": 1,
        "failed": 0,
        "stranded": [],
    }
    recorded.update(fields)
    groom_run.save(recorded, home)
    return recorded


def test_a_guard_that_runs_and_leaves_nothing_waiting_is_green(tmp_path: Path):
    saved_run(tmp_path, waiting=0)

    outcome = check_groom([guard_job()], tmp_path, NOW)

    assert outcome.severity == Severity.OK
    assert outcome.message == "Last run 1h ago, 0 chats waiting."


def test_the_green_line_states_the_age_and_the_waiting_queue(tmp_path: Path):
    """The one sentence the owner reads: how long ago it ran, and how many chats wait."""
    saved_run(tmp_path, at=NOW - 2 * HOUR_MS, waiting=3)

    outcome = check_groom([guard_job(lastRunAtMs=NOW - 2 * HOUR_MS)], tmp_path, NOW)

    assert outcome.severity == Severity.OK
    assert outcome.message == "Last run 2h ago, 3 chats waiting."


def test_no_job_at_all_is_a_failure(tmp_path: Path):
    """No job means no chat is compacted while it sits idle, whichever agent it belongs to."""
    outcome = check_groom([], tmp_path, NOW)

    assert outcome.severity == Severity.BROKEN
    assert "No hourly context guard job is registered" in outcome.message
    assert "nexgen openclaw setup" in outcome.action


def test_a_switched_off_job_is_a_failure_that_names_the_id(tmp_path: Path):
    outcome = check_groom([guard_job(enabled=False)], tmp_path, NOW)

    assert outcome.severity == Severity.BROKEN
    assert "switched off" in outcome.message
    assert "job-guard" in outcome.action


def test_a_job_that_never_ran_is_a_failure(tmp_path: Path):
    job = guard_job()
    del job["lastRunAtMs"]

    outcome = check_groom([job], tmp_path, NOW)

    assert outcome.severity == Severity.BROKEN
    assert "never finished a run" in outcome.message


def test_a_last_run_that_failed_is_a_failure(tmp_path: Path):
    """A command job records `error` for a non-zero exit, so the exit code is the run's verdict."""
    outcome = check_groom([guard_job(lastRunStatus="error")], tmp_path, NOW)

    assert outcome.severity == Severity.BROKEN
    assert "ended 'error'" in outcome.message


def test_a_last_success_older_than_three_hours_is_a_failure(tmp_path: Path):
    saved_run(tmp_path, at=NOW - 4 * HOUR_MS)

    outcome = check_groom([guard_job(lastRunAtMs=NOW - 4 * HOUR_MS)], tmp_path, NOW)

    assert outcome.severity == Severity.BROKEN
    assert "4h ago" in outcome.message
    assert "3h" in outcome.message


def test_three_hours_old_is_still_green(tmp_path: Path):
    """The window is the job's own promise: exactly on the edge is not late yet."""
    saved_run(tmp_path, at=NOW - 3 * HOUR_MS)
    outcome = check_groom([guard_job(lastRunAtMs=NOW - 3 * HOUR_MS)], tmp_path, NOW)

    assert outcome.severity == Severity.OK


def test_a_run_that_left_chats_waiting_and_compacted_none_is_a_failure(tmp_path: Path):
    """The stalled queue the groom itself alerts on, read here without waiting for the alert."""
    saved_run(tmp_path, waiting=2, compacted=0, failed=1)

    outcome = check_groom([guard_job()], tmp_path, NOW)

    assert outcome.severity == Severity.BROKEN
    assert "left 2 chats waiting and compacted none" in outcome.message


def test_a_run_that_left_no_record_is_a_warning_not_a_green(tmp_path: Path):
    outcome = check_groom([guard_job()], tmp_path, NOW)

    assert outcome.severity == Severity.WARN
    assert "left no record of its queue" in outcome.message


def test_grooming_switched_off_by_the_cap_is_a_warning_that_names_the_variable(tmp_path: Path):
    saved_run(tmp_path, cap=0, waiting=4, compacted=0)

    outcome = check_groom([guard_job()], tmp_path, NOW)

    assert outcome.severity == Severity.WARN
    assert "NEXGEN_OPENCLAW_MAX_COMPACTIONS=0" in outcome.message
    assert "4 chats wait" in outcome.message


def test_an_acp_chat_over_the_floor_is_named_as_never_compacted(tmp_path: Path):
    """Nothing can ask an ACP agent to compact, so the line says so instead of a green."""
    saved_run(tmp_path, stranded=[{"key": "agent:posta:acp:abc", "lane": "acp", "tokens": 912_000}])

    outcome = check_groom([guard_job()], tmp_path, NOW)

    assert outcome.severity == Severity.WARN
    assert "agent:posta:acp:abc: 912000 tokens, not compacted: lane 'acp' has no idle compaction" in outcome.message
    assert outcome.message.startswith("Last run 1h ago, 0 chats waiting.")

def test_an_acp_chat_below_the_floor_is_not_named(tmp_path: Path):
    """Under the floor a summary costs more than the cold cache, so it is not a finding."""
    saved_run(tmp_path, stranded=[{"key": "agent:posta:acp:abc", "lane": "acp", "tokens": IDLE_MIN_TOKENS - 1}])

    outcome = check_groom([guard_job()], tmp_path, NOW)

    assert outcome.severity == Severity.OK


def _refused(memory: dict, key: str, runtime: str, tokens: int, outcome: str) -> None:
    groom_memory.remember(memory, key, {"tokens": tokens, "stamp": 1_793_000_000_000.0}, outcome, runtime=runtime)


def test_a_codex_chat_the_gateway_refused_is_named_after_the_retries_stopped(tmp_path: Path):
    """`compacted=false` settles the memory, so the doctor is the only place that chat is visible."""
    saved_run(tmp_path)
    memory: dict = {}
    _refused(memory, "agent:capo:main", "codex", 905_000, groom_memory.NOT_COMPACTED)
    groom_memory.save_memory(memory, tmp_path)

    outcome = check_groom([guard_job()], tmp_path, NOW)

    assert outcome.severity == Severity.WARN
    assert "agent:capo:main: 905000 tokens, OpenClaw answered compacted=false (codex)" in outcome.message


def test_a_refusal_below_the_floor_or_on_a_direct_chat_is_not_named(tmp_path: Path):
    """Two reasons not to name it: nothing worth buying back, or a backend that just said no once."""
    saved_run(tmp_path)
    memory: dict = {}
    _refused(memory, "agent:main:main", "codex", IDLE_MIN_TOKENS - 1, groom_memory.NOT_COMPACTED)
    _refused(memory, "agent:posta:main", "openclaw", 905_000, groom_memory.NOT_COMPACTED)
    groom_memory.save_memory(memory, tmp_path)

    outcome = check_groom([guard_job()], tmp_path, NOW)

    assert outcome.severity == Severity.OK


def test_an_unread_scheduler_says_nothing_about_a_job_it_never_read(tmp_path: Path):
    """One unknown line is enough: the heartbeat check already reports the unread scheduler."""
    assert check_groom(None, tmp_path, NOW) is None


def test_an_unread_scheduler_still_catches_a_record_that_stopped_being_written(tmp_path: Path):
    """`nexgen doctor` asks no CLI, and the run's own record is what dates the last compaction."""
    saved_run(tmp_path, at=NOW - 5 * HOUR_MS)

    outcome = check_groom(None, tmp_path, NOW)

    assert outcome.severity == Severity.BROKEN
    assert "has written no run record for 5h" in outcome.message


def test_a_fresh_record_is_green_even_where_no_scheduler_was_asked(tmp_path: Path):
    saved_run(tmp_path, at=NOW - HOUR_MS)

    assert check_groom(None, tmp_path, NOW) is None


def _config() -> dict:
    """A configuration the engine itself would write, with one local model."""
    return {
        "plugins": {"allow": ["ollama"], "entries": {}},
        "models": {"providers": {"ollama": {
            "baseUrl": "http://127.0.0.1:11434", "api": "ollama", "models": [{"id": "qwen3:8b"}],
        }}},
        "agents": {"ownership": "explicit", "defaults": {"models": {}},
                   "entries": {"main": {"model": "ollama/qwen3:8b"}}},
        "tools": {"exec": {"security": "allowlist", "ask": "on-miss"}},
        "gateway": {"mode": "local", "port": 18789, "bind": "loopback",
                    "auth": {"mode": "token", "token": "a-real-token-value"},
                    "tailscale": {"mode": "off"}},
    }


def test_the_line_is_registered_in_the_doctor_and_reads_the_run_record(tmp_path: Path):
    """The registration is what makes the line exist at all: it must carry the record's facts."""
    import time

    from nexgen_core.openclaw.detect import Detection, Installation, LocalModelInfo, LocalModels, Login

    config_path = tmp_path / "home" / ".openclaw" / "openclaw.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(_config()), encoding="utf-8")
    # The doctor dates the run against the real clock here, so the fakes are relative to it.
    now = time.time() * 1000
    saved_run(tmp_path / "home", at=now - HOUR_MS, waiting=1, compacted=1)
    snapshot = Detection(
        openclaw=Installation(installed=True, version=None, config_path=str(config_path),
                              config_present=True),
        claude=Login(binary=False), codex=Login(binary=False), opencode_go=False,
        local=LocalModels(ollama=(LocalModelInfo(name="qwen3:8b"),)), n8n=False,
    )

    outcomes = collect(
        detected=snapshot, home=tmp_path / "home", vault_data=tmp_path / "vault",
        engine_root=tmp_path / "engine", env={}, platform="linux",
        connect=lambda *args: False, node_version=lambda: "v24.22.1",
        automations_reader=lambda: [guard_job(lastRunAtMs=int(now) - HOUR_MS)],
    )

    groom = {outcome.id: outcome for outcome in outcomes}[CHECK_ID]
    assert groom.severity == Severity.OK
    assert "1 chats waiting" in groom.message


def test_a_job_that_carries_only_the_iso_date_is_still_dated(tmp_path: Path):
    """`automations list --json` prints both shapes: the line reads whichever the release gives."""
    saved_run(tmp_path, at=NOW - HOUR_MS)
    job = guard_job()
    del job["lastRunAtMs"]
    job["lastRunAt"] = "2027-01-15T07:00:00.000Z"  # one hour before NOW

    outcome = check_groom([job], tmp_path, NOW)

    assert outcome.severity == Severity.OK
    assert outcome.message == "Last run 1h ago, 0 chats waiting."
