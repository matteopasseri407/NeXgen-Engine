"""The record's two judges: the doctor line and the release gate.

A release may not ship unless a harness inside OpenClaw was measured to cost what
it costs run directly, plus a light overhead. These tests pin the four states the
doctor distinguishes (no record, stale, measured on another OpenClaw, a failed
lane) and the four reasons the release preflight refuses, plus the one case where
it says nothing at all.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import i18n  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw import (  # noqa: E402 - sys.path shim for cloned checkout
    doctor_lanes,
    parity,
    parity_gate,
    parity_probe as probe,
    parity_record,
)
from nexgen_core.openclaw.detect import Detection, Installation, LocalModels, Login  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.openclaw.doctor import collect  # noqa: E402 - sys.path shim for cloned checkout
from nexgen_core.report import Severity  # noqa: E402 - sys.path shim for cloned checkout

#: The moment every record in this file is measured against.
NOW = 1_700_000_000.0

#: A record's date, written the way the command writes it, `days` before `NOW`.
def measured_days_ago(days: float) -> str:
    moment = datetime.fromtimestamp(NOW, timezone.utc) - timedelta(days=days)
    return moment.isoformat(timespec="seconds")


PASSING_LANE = parity_record.LaneResult(probe.AGY, "main", "google-antigravity-cli/gemini-3.8-flash",
                                 18307, 25417, parity_record.PASS)


@pytest.fixture(autouse=True)
def _english():
    """These tests compare the sentences a person reads, so they read English."""
    i18n.set_language("en")
    yield
    i18n.set_language(None)


def a_record(*lanes: parity_record.LaneResult, days_ago: float = 1.0,
             openclaw_version: str = "2026.11.2", engine_commit: str = "0123456789abcdef0") -> parity_record.Record:
    """One record as a run leaves it, dated where the test needs it."""
    return parity_record.Record(
        engine_commit=engine_commit,
        openclaw_version=openclaw_version,
        harness_versions={"google-antigravity-cli": "1.5.9"},
        measured_at=measured_days_ago(days_ago),
        lanes=lanes or (PASSING_LANE,),
    )


def store(path: Path, record: parity_record.Record) -> Path:
    """Put one record where the doctor and the gate read it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record.to_dict(), indent=2), encoding="utf-8")
    return path


def vault_record(tmp_path: Path, record: parity_record.Record) -> Path:
    """The Vault's own copy of a record."""
    return store(tmp_path / "vault" / "99-INDEX" / "OPENCLAW-PARITY.json", record)


# ---------------------------------------------------------------- the doctor


def test_no_record_warns_and_names_the_one_command(tmp_path):
    outcome = parity_gate.check(vault=tmp_path / "vault", home=tmp_path / "home", now=NOW)

    assert outcome.id == "openclaw.parity.tokens"
    assert outcome.severity == Severity.WARN
    assert "No OpenClaw token parity record" in outcome.message
    assert "'nexgen openclaw parity'" in outcome.action


def test_a_fresh_passing_record_is_ok_with_its_age_and_the_worst_overhead(tmp_path):
    vault_record(tmp_path, a_record(days_ago=2))

    outcome = parity_gate.check(vault=tmp_path / "vault", openclaw_version="2026.11.2 (b556fdf)",
                           home=tmp_path / "home", now=NOW)

    assert outcome.severity == Severity.OK
    assert outcome.message == (
        "OpenClaw token parity was measured 2 days ago: the worst lane adds 7,110 input tokens "
        "(google-antigravity-cli), under the 10,000 allowed."
    )


def test_a_record_of_one_day_is_described_in_the_singular(tmp_path):
    vault_record(tmp_path, a_record(days_ago=1))

    outcome = parity_gate.check(vault=tmp_path / "vault", openclaw_version="OpenClaw 2026.11.2 (b556fdf)",
                                home=tmp_path / "home", now=NOW)

    assert outcome.message.startswith("OpenClaw token parity was measured a day ago")


def test_a_record_older_than_seven_days_warns_even_when_it_passed(tmp_path):
    vault_record(tmp_path, a_record(days_ago=8))

    outcome = parity_gate.check(vault=tmp_path / "vault", openclaw_version="2026.11.2",
                           home=tmp_path / "home", now=NOW)

    assert outcome.severity == Severity.WARN
    assert "8 days old, and 7 is the limit" in outcome.message


def test_a_record_measured_on_another_openclaw_warns(tmp_path):
    vault_record(tmp_path, a_record(days_ago=1, openclaw_version="2026.11.2"))

    outcome = parity_gate.check(vault=tmp_path / "vault", openclaw_version="2026.12.0",
                           home=tmp_path / "home", now=NOW)

    assert outcome.severity == Severity.WARN
    assert "measured on OpenClaw 2026.11.2, and this machine runs 2026.12.0" in outcome.message


def test_a_failed_lane_is_broken_and_names_the_number(tmp_path):
    heavy = parity_record.LaneResult(probe.CLAUDE, "capo", "anthropic/claude-opus-5-5", 20000, 50000, parity_record.FAIL,
                              "OpenClaw adds 30,000 input tokens to this call, over the 10,000 the owner allows.")
    vault_record(tmp_path, a_record(PASSING_LANE, heavy))

    outcome = parity_gate.check(vault=tmp_path / "vault", openclaw_version="2026.11.2",
                           home=tmp_path / "home", now=NOW)

    assert outcome.severity == Severity.BROKEN
    assert "claude-cli (+30,000)" in outcome.message
    assert "no release ships on this record" in outcome.action


def test_a_record_without_a_date_warns(tmp_path):
    record = a_record()
    object.__setattr__(record, "measured_at", "")
    vault_record(tmp_path, record)

    outcome = parity_gate.check(vault=tmp_path / "vault", openclaw_version="2026.11.2",
                           home=tmp_path / "home", now=NOW)

    assert outcome.severity == Severity.WARN
    assert "carries no date" in outcome.message


def test_the_copy_this_machine_kept_is_read_when_the_vault_has_none(tmp_path, monkeypatch):
    state = tmp_path / "state"
    monkeypatch.setenv("AGENT_STATE_DIR", str(state))
    store(state / "nexgen" / "OPENCLAW-PARITY.json", a_record(days_ago=1))

    outcome = parity_gate.check(vault=tmp_path / "vault", openclaw_version="2026.11.2",
                           home=tmp_path / "home", now=NOW)

    assert outcome.severity == Severity.OK


def test_the_doctor_line_is_part_of_the_openclaw_doctor(tmp_path):
    config_path = tmp_path / "home" / ".openclaw" / "openclaw.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps({"agents": {"entries": {"main": {"model": "openai/gpt-6"}}}}),
                           encoding="utf-8")
    vault = tmp_path / "vault"
    (vault / "03-INFRA" / "agent-universal-layer").mkdir(parents=True)
    (vault / "03-INFRA" / "agent-universal-layer" / "modules.state.yaml").write_text(
        "schema_version: 2\nmodules:\n  openclaw: local\n", encoding="utf-8")

    outcomes = collect(
        detected=Detection(
            openclaw=Installation(installed=True, version="2026.11.2", config_path=str(config_path),
                                  config_present=True),
            claude=Login(binary=False), codex=Login(binary=False), opencode_go=False,
            local=LocalModels(), n8n=False,
        ),
        home=tmp_path / "home", vault_data=vault, engine_root=tmp_path / "engine",
        env={"LOCALAPPDATA": str(tmp_path / "localappdata"), "APPDATA": str(tmp_path / "appdata")},
        platform="linux", connect=lambda *_args: False, node_version=lambda: "v24.22.1",
        wsl_distributions=lambda: [],
    )

    parity_line = [outcome for outcome in outcomes if outcome.id == "openclaw.parity.tokens"]
    assert len(parity_line) == 1
    assert parity_line[0].severity == Severity.WARN


def test_the_lanes_come_from_the_module_that_owns_them():
    config = {"agents": {"defaults": {"models": {"anthropic/*": {"agentRuntime": {"id": "claude-cli"}}}},
                         "entries": {"capo": {"model": "anthropic/claude-opus-5-5"},
                                     "main": {"model": "openai/gpt-6"}}}}

    chosen = parity.lane_agents(config)

    assert list(chosen) == [probe.CLAUDE]
    readings = [doctor_lanes.lane_of(config, "capo")]
    assert chosen[probe.CLAUDE] == readings[0]


# ------------------------------------------------------------- the release


def test_a_passing_record_that_covers_this_release_refuses_nothing(tmp_path):
    vault_record(tmp_path, a_record(days_ago=1, engine_commit="abc123"))

    assert parity_gate.release_refusal(vault=tmp_path / "vault", is_ancestor=lambda commit: commit == "abc123",
                                 now=NOW) is None


def test_a_missing_record_refuses_the_release_and_names_the_command(tmp_path):
    refusal = parity_gate.release_refusal(vault=tmp_path / "vault", is_ancestor=lambda _commit: True, now=NOW)

    assert refusal is not None
    assert "99-INDEX" in refusal
    assert "run 'nexgen openclaw parity' on the gateway" in refusal


def test_a_failed_lane_refuses_the_release(tmp_path):
    heavy = parity_record.LaneResult(probe.CLAUDE, "capo", "anthropic/claude-opus-5-5", 20000, 50000, parity_record.FAIL)
    vault_record(tmp_path, a_record(PASSING_LANE, heavy))

    refusal = parity_gate.release_refusal(vault=tmp_path / "vault", is_ancestor=lambda _commit: True, now=NOW)

    assert refusal is not None
    assert "failed on claude-cli (+30,000)" in refusal


def test_a_record_that_measured_no_lane_refuses_the_release(tmp_path):
    nothing = parity_record.LaneResult(probe.AGY, verdict=parity_record.NOT_APPLICABLE,
                                       reason="No configured agent runs on this lane.")
    vault_record(tmp_path, a_record(nothing))

    refusal = parity_gate.release_refusal(vault=tmp_path / "vault", is_ancestor=lambda _commit: True, now=NOW)

    assert refusal is not None
    assert "measured no lane on both sides" in refusal


def test_a_record_older_than_seven_days_refuses_the_release(tmp_path):
    vault_record(tmp_path, a_record(days_ago=8))

    refusal = parity_gate.release_refusal(vault=tmp_path / "vault", is_ancestor=lambda _commit: True, now=NOW)

    assert refusal is not None
    assert "is 8 days old, and 7 days is the limit" in refusal


def test_a_record_measured_before_this_release_refuses_it(tmp_path):
    vault_record(tmp_path, a_record(days_ago=1, engine_commit="fedcba9"))

    refusal = parity_gate.release_refusal(vault=tmp_path / "vault", is_ancestor=lambda _commit: False, now=NOW)

    assert refusal is not None
    assert "measured on engine commit fedcba9, which is not part of this release" in refusal


def test_a_record_that_names_no_engine_commit_refuses_the_release(tmp_path):
    vault_record(tmp_path, a_record(days_ago=1, engine_commit=""))

    refusal = parity_gate.release_refusal(vault=tmp_path / "vault", is_ancestor=lambda _commit: True, now=NOW)

    assert refusal is not None
    assert "names no engine commit" in refusal


def test_the_release_preflight_asks_the_record_about_this_checkout(tmp_path, monkeypatch):
    from nexgen_core import paths, release

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "VERSION").write_text("9.9.9\n", encoding="utf-8")
    _git(repo, "add", "VERSION")
    _git(repo, "commit", "-q", "-m", "the release being cut")
    head = _git(repo, "rev-parse", "HEAD").strip()
    vault = tmp_path / "vault"
    monkeypatch.setattr(paths, "resolve_vault_data", lambda *_args, **_kwargs: vault)
    assert release._parity_problem(repo) is not None

    # The preflight reads the clock, not a test's moment, so the record is dated today.
    today = datetime.now(timezone.utc).isoformat(timespec="seconds")
    vault_record(tmp_path, parity_record.Record(engine_commit=head, openclaw_version="2026.11.2",
                                         measured_at=today, lanes=(PASSING_LANE,)))

    assert release._parity_problem(repo) is None


def test_the_preflight_refuses_with_one_line_naming_the_command(monkeypatch, capsys):
    from nexgen_core import release

    monkeypatch.setattr(release, "_parity_problem",
                        lambda _repo: "there is no OpenClaw parity record, run 'nexgen openclaw parity'")
    monkeypatch.setattr(release.subprocess, "run",
                        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "", ""))

    assert release._preflight() == 1
    assert ("the OpenClaw parity record does not cover this release: there is no OpenClaw parity record, "
            "run 'nexgen openclaw parity'") in capsys.readouterr().err


def _git(repo: Path, *args: str) -> str:
    """One git command in `repo`, with an identity so a commit works on a bare CI machine."""
    result = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=NeXgen", "-c", "user.email=nexgen" "@example.invalid", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True, timeout=60,
    )
    return result.stdout
