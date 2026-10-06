"""The guard cycle runs every phase, each isolated, and says which ones failed.

It used to run as one chain: a skill whose GitHub fetch timed out stopped the MCP
render, the permission posture and the guardrail hook behind it, every 30 minutes,
for as long as the network stayed down. A failure in one phase has to be a
failure of that phase.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from nexgen_core import action_notes
from nexgen_core.checks.takeover_checks import check_last_cycle_phases
from nexgen_core.guard import GuardMode, GuardRunner
from nexgen_core.report import Severity

PHASES = ("skills", "mcp", "permissions", "instructions", "launchers", "scheduler", "modules")


def _runner(tmp_path: Path, monkeypatch) -> tuple[GuardRunner, list[str]]:
    """A runner whose phases only record that they ran: nothing touches the host."""
    vault = tmp_path / "vault"
    vault.mkdir()
    runner = GuardRunner(vault_data=vault, home=tmp_path / "home")
    monkeypatch.setattr(runner, "_phase_git", lambda *_a: None)
    monkeypatch.setattr(runner, "_phase_preflight", lambda *_a: None)
    ran: list[str] = []
    for name in PHASES:
        monkeypatch.setattr(runner, f"_phase_{name}", lambda *_a, _n=name: ran.append(_n))
    return runner, ran


@pytest.mark.parametrize("broken", PHASES)
def test_one_phase_failing_never_stops_the_others(tmp_path, monkeypatch, broken):
    runner, ran = _runner(tmp_path, monkeypatch)

    def explode(*_a):
        raise RuntimeError(f"{broken} exploded")

    monkeypatch.setattr(runner, f"_phase_{broken}", explode)

    result = runner.run(GuardMode.APPLY)

    assert ran == [name for name in PHASES if name != broken]  # all the others, in order
    assert result.success is False and result.exit_code == 1  # still a failed cycle: the unit alerts
    # Not asserting on translated wording: the suite runs under whatever language the machine speaks.
    assert broken in result.message
    assert any(action_notes.is_error(a) and f"'{broken}'" in a and f"{broken} exploded" in a
               for a in result.actions_taken)
    assert runner.heartbeat.recorded_failed_phases() == [broken]


def test_a_cycle_where_everything_works_records_no_failed_phase(tmp_path, monkeypatch):
    runner, ran = _runner(tmp_path, monkeypatch)

    result = runner.run(GuardMode.APPLY)

    assert result.success is True and ran == list(PHASES)
    assert runner.heartbeat.recorded_failed_phases() == []
    assert "FAIL=" not in runner.heartbeat.liveness_file.read_text(encoding="utf-8")


def test_several_failures_are_all_reported(tmp_path, monkeypatch):
    runner, ran = _runner(tmp_path, monkeypatch)
    for name in ("skills", "launchers"):
        monkeypatch.setattr(runner, f"_phase_{name}", lambda *_a: (_ for _ in ()).throw(OSError("denied")))

    result = runner.run(GuardMode.APPLY)

    assert runner.heartbeat.recorded_failed_phases() == ["skills", "launchers"]
    assert "skills, launchers" in result.message
    assert ran == ["mcp", "permissions", "instructions", "scheduler", "modules"]


def test_the_guardrail_phase_runs_even_when_skills_cannot_be_fetched(tmp_path, monkeypatch):
    """The point of the whole change: a network error in a skill fetch must not
    leave the safety controls unapplied."""
    runner, ran = _runner(tmp_path, monkeypatch)
    monkeypatch.setattr(runner, "_phase_skills", lambda *_a: (_ for _ in ()).throw(TimeoutError("github unreachable")))

    runner.run(GuardMode.GUARD)

    assert "permissions" in ran


def test_a_crash_inside_the_permissions_phase_is_a_failed_phase_not_a_warning(tmp_path, monkeypatch):
    """The guardrail hook may not have been applied: that is a failed cycle, not
    "completed with warnings" (it used to be caught and downgraded to a warning)."""
    runner, ran = _runner(tmp_path, monkeypatch)
    monkeypatch.setattr(runner, "_phase_permissions", GuardRunner._phase_permissions.__get__(runner))
    monkeypatch.setattr(runner, "apply_runtime_permissions", lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    result = runner.run(GuardMode.APPLY)

    assert result.success is False and result.exit_code == 1
    assert runner.heartbeat.recorded_failed_phases() == ["permissions"]
    assert "scheduler" in ran and "modules" in ran  # and the cycle went on


def test_warnings_are_counted_whatever_spelling_the_emitter_used(tmp_path, monkeypatch):
    """skill_sources wrote "[WARNING]" while the guard counted "[WARN]" and "[AVVISO]":
    a skill warning was not counted and the cycle was reported as a plain success."""
    runner, _ran = _runner(tmp_path, monkeypatch)
    monkeypatch.setattr(runner, "_phase_skills", lambda actions: actions.append("[WARNING] pin drifted"))

    result = runner.run(GuardMode.APPLY)

    assert result.success is True
    assert "warn" in result.message.lower() or "avvis" in result.message.lower()
    assert runner.heartbeat.recorded_warnings() == 1


# ----------------------------------------------------------------- liveness


def test_liveness_keeps_its_old_format_and_adds_the_failed_phases(tmp_path):
    from nexgen_core.beat import Heartbeat

    beat = Heartbeat(state_dir=tmp_path / "state")
    beat.record_liveness(warnings=2, failed_phases=["skills", "mcp"])

    lines = beat.liveness_file.read_text(encoding="utf-8").splitlines()
    float(lines[0])  # a previous release reads only the first line and must still parse it
    assert lines[2] == "WARN=2" and lines[3] == "FAIL=skills,mcp"
    assert beat.recorded_warnings() == 2
    assert beat.recorded_failed_phases() == ["skills", "mcp"]
    ok, message = beat.check_liveness()
    assert ok is True  # alive: the unit's own failure is what alerts
    assert "skills, mcp" in message


def test_liveness_file_is_replaced_atomically(tmp_path, monkeypatch):
    from nexgen_core import files
    from nexgen_core.beat import Heartbeat

    beat = Heartbeat(state_dir=tmp_path / "state")
    beat.record_liveness()
    before = beat.liveness_file.read_text(encoding="utf-8")
    monkeypatch.setattr(files.os, "replace", lambda *_a: (_ for _ in ()).throw(OSError("synthetic")))

    with pytest.raises(OSError):
        beat.record_liveness(warnings=9)

    assert beat.liveness_file.read_text(encoding="utf-8") == before


def test_doctor_says_which_phases_the_last_cycle_could_not_complete(tmp_path):
    from nexgen_core.beat import Heartbeat

    state = tmp_path / "state"
    beat = Heartbeat(state_dir=state)
    beat.record_liveness()
    assert check_last_cycle_phases(state).severity == Severity.OK

    beat.record_liveness(failed_phases=["skills"])
    outcome = check_last_cycle_phases(state)
    assert outcome.severity == Severity.BROKEN and "skills" in outcome.message and outcome.action


# --------------------------------------------------------------- one definition


def test_note_severity_is_written_and_read_in_one_module():
    assert action_notes.is_warning(action_notes.WARN + "x")
    assert action_notes.is_error(action_notes.ERROR + "x")
    for spelling in ("[WARNING] x", "[AVVISO] x"):
        assert action_notes.is_warning(spelling) and not action_notes.is_error(spelling)
    assert action_notes.is_error("[ERRORE] x") and not action_notes.is_warning("[ERRORE] x")
    assert not action_notes.is_warning("plain") and not action_notes.is_error("plain")


def test_no_other_module_spells_the_severity_prefixes_by_hand():
    """69 places wrote or matched these by hand and drifted apart. A new emitter has
    to use action_notes, or the guard will not count what it says. Only a literal that
    *starts* with the marker counts: a sentence that mentions "[ERROR]" in passing is prose."""
    root = Path(action_notes.__file__).resolve().parent
    by_hand = re.compile(r"""["']\[(?:WARN|WARNING|ERROR|AVVISO|ERRORE)\]""")
    offenders = [
        f"{path.relative_to(root)}:{n}"
        for path in root.rglob("*.py")
        if path.name != "action_notes.py"
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if by_hand.search(line)
    ]
    assert offenders == []
