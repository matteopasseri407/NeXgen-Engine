"""The gateway's database migration between the install and the restart.

From 2026.11.2 a gateway on an unmigrated database refuses to start until `openclaw doctor
--fix` has run (docs/cli/update.md: install, doctor --fix, restart). These tests drive the
alignment with a fake runner and a recording gateway service: nothing is installed, stopped
or started, and no doctor runs. The order of the calls is the thing under test.
"""
from __future__ import annotations

import pytest

from nexgen_core import i18n
from nexgen_core.openclaw import gateway_doctor, version_align
from nexgen_core.openclaw_service import RunResult
from test_nexgen_openclaw_version_align import (
    NIGHT,
    OLD,
    PIN,
    FakeRun,
    a_machine,
    an_install,
    as_gateway,
    installs,
    lookup,
)


@pytest.fixture(autouse=True)
def _fake_engine(tmp_path, monkeypatch):
    """The pin is data on a fake engine, and the host may be mutated inside the test."""
    monkeypatch.delenv("NEXGEN_DISABLE_HOST_MUTATIONS", raising=False)
    machine = a_machine(tmp_path)
    monkeypatch.setenv("AGENT_ENGINE_ROOT", str(machine.engine))


@pytest.fixture(autouse=True)
def _english():
    """The sentences are compared in the source language, on any machine."""
    i18n.set_language("en")
    yield
    i18n.set_language(None)


class OrderedService:
    """A gateway service that records stop and restart into the same list as the doctor runs."""

    def __init__(self, events: list[str], stop_ok: bool = True):
        self.events = events
        self.stop_ok = stop_ok
        self.restarted = 0

    def stop(self, **_kwargs):
        self.events.append("stop")
        return {"ok": self.stop_ok}

    def restart(self, **_kwargs):
        self.events.append("restart")
        self.restarted += 1
        return {"ok": True, "error": None}


class DoctorRun(FakeRun):
    """The fake runner, plus `openclaw doctor` answering with the exit codes it is given, in order."""

    def __init__(self, machine, events: list[str], codes: tuple[int, ...] = (0,), **kwargs):
        super().__init__(machine, **kwargs)
        self.events = events
        self.codes = list(codes)

    def __call__(self, argv, env=None) -> RunResult:
        if argv[1:2] == ["doctor"]:
            self.calls.append(list(argv))
            self.events.append("doctor")
            code = self.codes.pop(0) if self.codes else 0
            return RunResult(code, "", "" if code == 0 else "Error: a migration stopped")
        return super().__call__(argv, env)


def upgrade(tmp_path, *, codes=(0,), stop_ok=True, smoke=0):
    """A gateway that is behind the pin, in the night window, aligned with a fake doctor and service."""
    machine = a_machine(tmp_path)
    as_gateway(machine)
    events: list[str] = []
    fake = DoctorRun(machine, events, codes=codes)
    service = OrderedService(events, stop_ok=stop_ok)
    outcome = version_align.align(
        machine.home, machine.engine, env={}, platform="linux", which=lookup(machine), run=fake,
        service=service, smoke=lambda: smoke, clock=lambda: NIGHT,
    )
    return machine, fake, service, events, outcome


def doctors(calls: list[list[str]]) -> list[list[str]]:
    return [call for call in calls if call[1:2] == ["doctor"]]


def test_the_gateway_is_stopped_migrated_and_restarted_in_that_order(tmp_path):
    machine, fake, _service, events, outcome = upgrade(tmp_path)

    assert outcome.state == version_align.ALIGNED
    assert events == ["stop", "doctor", "restart"]
    assert doctors(fake.calls) == [[str(machine.bin / "openclaw"), "doctor", "--fix", "--non-interactive"]]


def test_a_doctor_that_fails_once_is_asked_a_second_time(tmp_path):
    machine, fake, _service, events, outcome = upgrade(tmp_path, codes=(1, 0))

    assert outcome.state == version_align.ALIGNED
    assert events == ["stop", "doctor", "doctor", "restart"]
    assert len(doctors(fake.calls)) == 2


def test_a_doctor_that_fails_twice_puts_the_previous_release_back(tmp_path):
    machine, fake, service, events, outcome = upgrade(tmp_path, codes=(1, 1))

    assert outcome.state == version_align.FAILED
    assert events == ["stop", "doctor", "doctor", "restart"]  # the restart is the rollback's
    assert installs(fake.calls) == [an_install(machine.bin / "npm", PIN), an_install(machine.bin / "npm", OLD)]
    assert "was put back" in outcome.line
    assert "already migrated" not in outcome.line
    assert service.restarted == 1
    assert (machine.state / version_align.STATE_FILE).is_file()


def test_a_migration_followed_by_a_failed_check_is_never_reported_as_a_clean_rollback(tmp_path):
    machine, fake, _service, events, outcome = upgrade(tmp_path, codes=(0,), smoke=1)

    assert outcome.state == version_align.FAILED
    assert events == ["stop", "doctor", "restart", "restart"]
    assert "already migrated" in outcome.line
    assert "does not undo that" in outcome.line
    assert "was put back" not in outcome.line
    # The previous release is still reinstalled: the message says what that cannot repair.
    assert installs(fake.calls)[-1] == an_install(machine.bin / "npm", OLD)


def test_a_gateway_that_does_not_stop_is_not_migrated(tmp_path):
    _machine, _fake, _service, events, outcome = upgrade(tmp_path, stop_ok=False)

    assert outcome.state == version_align.FAILED
    assert "doctor" not in events
    assert "the gateway did not stop" in outcome.line
    assert "was put back" in outcome.line


def test_migrate_reports_success_only_after_doctor_exits_zero():
    calls: list[list[str]] = []

    def run(argv, env):
        calls.append(list(argv))
        return RunResult(0, "", "")

    migration = gateway_doctor.migrate(OrderedService([]), "/bin/openclaw", run, {})

    assert migration == gateway_doctor.Migration(migrated=True)
    assert calls == [["/bin/openclaw", "doctor", "--fix", "--non-interactive"]]


def test_migrate_without_a_service_controller_migrates_nothing():
    migration = gateway_doctor.migrate(None, "openclaw", lambda argv, env: RunResult(0, "", ""), {})

    assert migration.migrated is False
    assert "service controller is not available" in migration.problem


def test_the_failure_line_names_the_migration_only_when_one_happened():
    plain = gateway_doctor.failure_line("2026.11.2", "2026.11.1", "smoke test failed", " Done.", migrated=False)
    migrated = gateway_doctor.failure_line("2026.11.2", "2026.11.1", "smoke test failed", "", migrated=True)

    assert plain == "OpenClaw 2026.11.2 did not work here (smoke test failed): 2026.11.1 was put back. Done."
    assert "already migrated" in migrated
    assert "was put back" not in migrated
