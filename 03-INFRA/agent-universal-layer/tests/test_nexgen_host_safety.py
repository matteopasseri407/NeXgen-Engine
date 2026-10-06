"""The guarantees that make it safe to keep adding to the engine.

One lock that every path which changes the machine goes through, runs that
cannot hang forever, and an unattended update that undoes itself when it fails
halfway. Each used to be true of some paths and not of others, and green tests
did not say so because they exercised each path alone.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
from nexgen_core import doctor as doctor_module
from nexgen_core import scheduler
from nexgen_core.lock import LOCK_FILENAME, HostLock, LockTimeoutError, host_mutation
from nexgen_core.report import Report
from test_nexgen_phase5 import _env, _git, _load_updater, _upgrade_fixture
from test_nexgen_update_command import _tree_entry


def _no_prompt(_text: str) -> str:
    raise AssertionError("unattended mode must never ask for confirmation")


# --------------------------------------------------------------------------
# Runs are bounded.
# --------------------------------------------------------------------------

def test_units_have_start_timeouts_and_a_guard_cycle_cannot_overlap_the_next(tmp_path):
    """systemd disables the start timeout for Type=oneshot unless it is set, so a
    wedged guard was never killed, kept the lock, and made every later cycle
    exit 0 without doing anything."""
    home, vault = tmp_path / "home", tmp_path / "home" / "V"
    guard = scheduler._systemd_service_content(home, vault / "03-INFRA", vault, vault)
    beat = scheduler._systemd_heartbeat_content(home, vault / "03-INFRA", vault, vault)

    def minutes(unit: str) -> int:
        match = re.search(r"^TimeoutStartSec=(\d+)min$", unit, re.MULTILINE)
        assert match, unit
        return int(match.group(1))

    guard_interval = int(re.search(r"OnUnitActiveSec=(\d+)min", scheduler._SYSTEMD_TIMER).group(1))
    assert minutes(guard) < guard_interval
    assert "OnUnitActiveSec=1h" in scheduler._SYSTEMD_HEARTBEAT_TIMER
    assert minutes(beat) < 60


# --------------------------------------------------------------------------
# One lock.
# --------------------------------------------------------------------------

def test_every_mutating_path_names_the_same_lock_and_the_operator_override_moves_all_of_them(tmp_path, monkeypatch):
    state = tmp_path / "state"
    assert host_mutation("anything", state_dir=state).lock_path == state / LOCK_FILENAME

    elsewhere = tmp_path / "elsewhere.lock"
    monkeypatch.setenv("AGENT_SYNC_LOCK_FILE", str(elsewhere))
    # The override used to apply to HostLock() only, and the guard ignored it.
    assert host_mutation("anything", state_dir=state).lock_path == elsewhere
    assert HostLock().lock_path == elsewhere


def test_the_guard_waits_behind_any_other_mutation_not_only_behind_another_guard(tmp_path, monkeypatch):
    from nexgen_core.guard import GuardMode, GuardRunner

    monkeypatch.setenv("AGENT_SYNC_LOCK_TIMEOUT_SECONDS", "0.1")
    runner = GuardRunner(vault_data=tmp_path / "vault", engine_root=tmp_path / "engine", home=tmp_path / "home")
    with host_mutation("something-else", state_dir=runner.state_dir, timeout=5):
        manual = runner.run(GuardMode.APPLY)
        timer = runner.run(GuardMode.GUARD)

    assert (manual.success, manual.exit_code) == (False, 75)  # a person is told to retry
    assert (timer.success, timer.exit_code) == (True, 0)  # the timer just comes back in 30 minutes


def test_doctor_fix_skips_its_remedies_while_another_sync_runs_and_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor_module, "REMEDY_LOCK_WAIT_SECONDS", 0.1)
    seen: list[bool] = []
    monkeypatch.setattr(doctor_module.Doctor, "_diagnose", lambda self, apply: seen.append(apply) or Report())
    doctor = doctor_module.Doctor(home=tmp_path / "home", vault_data=tmp_path / "vault")

    with host_mutation("a-guard-cycle", state_dir=doctor.state_dir, timeout=5):
        busy = doctor.run_diagnostics(apply_remedies=True)
    free = doctor.run_diagnostics(apply_remedies=True)

    assert seen == [False, True]  # read-only while busy, with remedies once free
    assert [o.id for o in busy.outcomes] == ["doctor.fix.skipped"]
    assert free.outcomes == []
    with host_mutation("proof-it-was-released", state_dir=doctor.state_dir, timeout=1):
        pass


def test_the_silent_pin_bump_does_not_run_while_a_guard_cycle_holds_the_lock(tmp_path, monkeypatch):
    """The bump rewrites manifests and re-materializes skills, as a guard cycle does.
    Its own lock is free here: only the host lock can be what stops it."""
    from nexgen_core import lock as lock_module
    from nexgen_core import thirdparty_bump

    monkeypatch.setattr(thirdparty_bump, "collect_plan", lambda payload: ([{"what": "x"}], [], []))

    def must_not_run(*_a, **_k):
        raise AssertionError("the bump rewrote manifests while the host lock was held elsewhere")

    monkeypatch.setattr(thirdparty_bump, "apply_plan", must_not_run)
    real = lock_module.host_mutation  # the bump waits 30s for the host lock; the test will not
    monkeypatch.setattr(lock_module, "host_mutation", lambda name, **kw: real(name, **{**kw, "timeout": 0.1}))
    home, state = tmp_path / "home", tmp_path / "state"

    with host_mutation("a-guard-cycle", state_dir=state, timeout=5):
        answer = thirdparty_bump.auto_apply({}, home / "vault", home, state)

    assert answer == {"ok": True, "applied": 0, "busy": True}


# --------------------------------------------------------------------------
# The unattended update waits its turn, and undoes itself when it fails.
# --------------------------------------------------------------------------

def _verified(updater, monkeypatch) -> None:
    monkeypatch.setattr(updater, "_release_verdict", lambda *_a: updater.TagVerdict("verified", "test"))


def _release_whose_apply_fails(tmp_path: Path) -> tuple[Path, Path]:
    origin, engine = _upgrade_fixture(tmp_path, "0.1.0", "0.1.1")
    entry = origin / "03-INFRA" / "scripts" / "nexgen_core" / "cli" / "__init__.py"
    text = entry.read_text(encoding="utf-8")
    assert "elif verbs[:1] == ['apply']:\n    pass\n" in text
    entry.write_text(text.replace("elif verbs[:1] == ['apply']:\n    pass\n",
                                  "elif verbs[:1] == ['apply']:\n    sys.exit(3)\n"), encoding="utf-8")
    _git(origin, "commit", "-qam", "release whose provisioning fails")
    _git(origin, "tag", "-f", "v0.1.1")
    return origin, engine


def test_update_waits_for_a_running_sync_instead_of_merging_under_it(tmp_path, monkeypatch, capsys):
    updater = _load_updater()
    _verified(updater, monkeypatch)
    monkeypatch.setattr(updater, "UPDATE_LOCK_WAIT_SECONDS", 0.2)
    _origin, engine = _upgrade_fixture(tmp_path, "0.1.0", "0.1.1")
    env = _env(engine)
    before = _git(engine, "rev-parse", "HEAD").stdout.strip()

    with host_mutation("a-guard-cycle", state_dir=Path(env["AGENT_STATE_DIR"]), timeout=5):
        unattended = updater.main(["--unattended"], environ=env, input_fn=_no_prompt)
        interactive = updater.main(["--yes"], environ=env)

    assert unattended == 0  # not a failure: the next beat tries again
    assert interactive == 75  # a person is told to retry
    assert _git(engine, "rev-parse", "HEAD").stdout.strip() == before
    assert "Another sync is running" in capsys.readouterr().out


def test_unattended_update_that_fails_after_the_merge_is_undone_and_not_retried_every_hour(
    tmp_path, monkeypatch, capsys
):
    updater = _load_updater()
    _verified(updater, monkeypatch)
    _origin, engine = _release_whose_apply_fails(tmp_path)
    env = _env(engine)
    before = _git(engine, "rev-parse", "HEAD").stdout.strip()

    result = updater.main(["--unattended"], environ=env, input_fn=_no_prompt)

    assert result == updater.EXIT_ROLLED_BACK == 71
    assert _git(engine, "rev-parse", "HEAD").stdout.strip() == before
    assert (engine / "VERSION").read_text(encoding="utf-8").strip() == "0.1.0"
    assert _git(engine, "status", "--porcelain").stdout == ""
    rejected = json.loads((Path(env["AGENT_STATE_DIR"]) / updater.REJECTED_FILE).read_text(encoding="utf-8"))
    assert rejected["target"] == "v0.1.1"
    assert "going back to v0.1.0" in capsys.readouterr().err

    # The next beat does not merge, fail and roll back again.
    assert updater.main(["--unattended"], environ=env, input_fn=_no_prompt) == 0
    assert _git(engine, "rev-parse", "HEAD").stdout.strip() == before
    assert "unattended updates skip it" in capsys.readouterr().out


def test_interactive_update_never_rolls_back_on_its_own(tmp_path, monkeypatch, capsys):
    """A person is there to look first; this contract did not change."""
    updater = _load_updater()
    _verified(updater, monkeypatch)
    _origin, engine = _release_whose_apply_fails(tmp_path)
    before = _git(engine, "rev-parse", "HEAD").stdout.strip()

    result = updater.main(["--yes"], environ=_env(engine))

    assert result == 1
    assert _git(engine, "rev-parse", "HEAD").stdout.strip() != before
    assert "will not roll back automatically" in capsys.readouterr().err


def test_a_successful_update_forgets_the_rejected_release(tmp_path, monkeypatch):
    updater = _load_updater()
    _verified(updater, monkeypatch)
    _origin, engine = _upgrade_fixture(tmp_path, "0.1.0", "0.1.1")
    env = _env(engine)
    state = Path(env["AGENT_STATE_DIR"])
    state.mkdir(parents=True)
    (state / updater.REJECTED_FILE).write_text(json.dumps({"target": "v0.1.1"}), encoding="utf-8")

    assert updater.main(["--yes"], environ=env) == 0

    assert not (state / updater.REJECTED_FILE).exists()


def test_a_rollback_that_itself_fails_is_reported_as_the_worst_case(tmp_path, monkeypatch, capsys):
    updater = _load_updater()
    _verified(updater, monkeypatch)
    _origin, engine = _release_whose_apply_fails(tmp_path)
    real_git = updater._git

    def reset_fails(repo, *args, **kwargs):
        if args and args[0] == "reset":
            raise updater.UpdateError("synthetic: cannot reset")
        return real_git(repo, *args, **kwargs)

    monkeypatch.setattr(updater, "_git", reset_fails)

    result = updater.main(["--unattended"], environ=_env(engine), input_fn=_no_prompt)

    assert result == updater.EXIT_ROLLBACK_FAILED == 72
    assert "reset --hard" in capsys.readouterr().err  # the manual recovery is printed


def test_the_engine_pin_goes_back_with_the_engine(tmp_path, monkeypatch):
    updater = _load_updater()
    _verified(updater, monkeypatch)
    _origin, engine = _upgrade_fixture(tmp_path, "0.1.0", "0.1.1")
    previous = _git(engine, "rev-parse", "HEAD").stdout.strip()
    data = tmp_path / "data"
    pin = data / "99-INDEX" / "ENGINE-PIN.txt"
    pin.parent.mkdir(parents=True)
    _git(data, "init", "-b", "main")
    pin.write_text(f"{previous}\n", encoding="utf-8")
    _git(data, "add", "99-INDEX/ENGINE-PIN.txt")
    _git(data, "commit", "-m", "seed engine pin")
    entry, real_run, events = _tree_entry(engine), updater._run, []

    def commands(args, **kwargs):
        if args[: len(entry) + 1] == entry + ["vault"]:
            events.append(("pin", pin.read_text(encoding="utf-8").strip()))
            _git(data, "add", "--", args[-1])
            _git(data, "commit", "-qm", "publish engine pin", "--allow-empty")
            return subprocess.CompletedProcess(args, 0, "", "")
        if args == entry + ["apply"]:
            events.append(("apply", ""))
            # Only the first provisioning (the new release) fails.
            return subprocess.CompletedProcess(args, 1 if sum(e[0] == "apply" for e in events) == 1 else 0, "", "")
        return real_run(args, **kwargs)

    monkeypatch.setattr(updater, "_run", commands)
    monkeypatch.setattr(updater, "_doctor", lambda *_a, **_k: (0, 0))

    result = updater.main(
        ["--unattended"], environ={**_env(engine), "AGENT_VAULT_DATA": str(data)}, input_fn=_no_prompt,
    )

    assert result == updater.EXIT_ROLLED_BACK
    assert _git(engine, "rev-parse", "HEAD").stdout.strip() == previous
    assert pin.read_text(encoding="utf-8").strip() == previous
    assert [e[0] for e in events] == ["pin", "apply", "pin", "apply"]  # new pin, failed apply, old pin, realign


def test_lock_wait_is_a_timeout_error_so_callers_can_tell_busy_from_broken():
    assert issubclass(LockTimeoutError, TimeoutError)


@pytest.mark.parametrize("code", [70, 71, 72])
def test_heartbeat_turns_the_three_update_outcomes_that_need_a_person_into_alerts(tmp_path, code):
    from nexgen_core.beat import Heartbeat

    beat = Heartbeat(home=tmp_path / "home", vault_data=tmp_path / "vault", engine_root=tmp_path / "engine")
    alerts: list[dict] = []
    beat.megaphone.send_alert = lambda **kw: alerts.append(kw)

    beat._alert_on_update_outcome(code)
    for quiet in (0, 1, 75):
        beat._alert_on_update_outcome(quiet)

    assert len(alerts) == 1
    assert alerts[0]["alert_key"] in {"update_refused", "update_rolled_back", "update_rollback_failed"}
    assert alerts[0]["title"] and alerts[0]["message"] and alerts[0]["action"]
