"""The guard keeps every machine running OpenClaw on the release the engine pins.

The defect this pins: the pin was only ever applied by `nexgen openclaw setup`, run by
hand, so a VPS gateway ran the pinned release while a desktop node stayed weeks behind
and nothing in the recurring cycle noticed. These tests drive the decision with fake
processes: no npm, no systemd and no gateway is touched.

They also pin when the cycle may touch anything at all. On a gateway a replace happens only
inside the local night window, with no agent session and no automation running. A node waits
for no window, because a PC that is off at night would never update: it waits only for its
own service to be idle, that is for the pid `openclaw node status --json` reports to have no
child process, since the command an agent runs on the node is a child of it. A state that
cannot be read postpones the cycle instead of passing for an idle machine.

Since 2026-11-15 the install itself is OpenClaw's own `update --tag <pin>`: these tests assert
that command, not an npm invocation, and that a release the updater refuses to own, or a
release that does not answer here, is reported in the updater's own words.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest

from nexgen_core.openclaw import align_node, align_window, version_align
from nexgen_core.openclaw_service import RunResult

PIN = "2026.10.7"
OLD = "2026.10.6"
NODE_RANGE = ">=24.22.1 <25 || >=26.12.1"
#: 04:00 and 14:00 local: inside and outside the 03:00-06:00 window a gateway waits for.
NIGHT = datetime(2026, 10, 30, 4, 0).timestamp()
DAY = datetime(2026, 10, 30, 14, 0).timestamp()
#: The pid the node service reports, as `openclaw node status --json` prints it.
SERVICE_PID = 474701
#: What the updater prints when it accepted the pin (`run.status`, from the 2026.11.2
#: release's own vocabulary: succeeded, failed, rolled-back, skipped).
APPLIED = json.dumps({"run": {"status": "succeeded"}, "currentVersion": PIN})


def an_update(binary: Path | str, version: str) -> list[str]:
    """The one argv `version_align` produces for a release, written out as a literal."""
    return [str(binary), "update", "--tag", version, "--json", "--yes"]


@dataclass
class Machine:
    """A fake home: one npm prefix with OpenClaw in it, and the bin folder that owns it."""

    home: Path
    bin: Path
    root: Path
    engine: Path
    state: Path


def a_machine(tmp_path: Path, version: str = OLD, node: str = "v24.22.1") -> Machine:
    """The minimum this phase reads: a global package, the bin beside it, a pin."""
    home = tmp_path / "home"
    state = home / ".openclaw"
    state.mkdir(parents=True, exist_ok=True)
    (state / "openclaw.json").write_text("{}", encoding="utf-8")
    folder = home / ".local" / "bin"
    folder.mkdir(parents=True, exist_ok=True)
    for name in ("node", "npm", "openclaw"):
        (folder / name).write_text("#!/bin/sh\n", encoding="utf-8")
    root = home / ".npm-global" / "lib" / "node_modules"
    (root / "openclaw").mkdir(parents=True, exist_ok=True)
    (root / "openclaw" / "package.json").write_text(
        json.dumps({"version": version, "engines": {"node": NODE_RANGE}}), encoding="utf-8")
    engine = tmp_path / "engine"
    pin = engine / "agent-universal-layer" / "openclaw" / "pin.json"
    pin.parent.mkdir(parents=True, exist_ok=True)
    pin.write_text(json.dumps({"version": PIN, "node": NODE_RANGE}), encoding="utf-8")
    return Machine(home=home, bin=folder, root=root, engine=engine, state=home / ".local" / "state")


@pytest.fixture(autouse=True)
def _fake_engine(tmp_path: Path, monkeypatch):
    """The pin is data on this fake engine, and a test may perform the install."""
    monkeypatch.delenv("NEXGEN_DISABLE_HOST_MUTATIONS", raising=False)
    machine = a_machine(tmp_path)
    monkeypatch.setenv("AGENT_ENGINE_ROOT", str(machine.engine))
    return machine


@pytest.fixture(autouse=True)
def _english():
    """The sentences a person reads are compared in the source language, on any machine."""
    from nexgen_core import i18n

    i18n.set_language("en")
    yield
    i18n.set_language(None)


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    """The pause between two node checks is skipped: a test counts attempts, not seconds."""
    monkeypatch.setattr(align_node, "_pause", lambda _seconds: None)


#: What OpenClaw prints when a command fails while its state database is being migrated.
BUSY = "[openclaw] The CLI command failed.\n[openclaw] For help, run `openclaw doctor`."


class FakeRun:
    """Every external program this phase starts, answered from one place."""

    def __init__(self, machine: Machine, *, node: str = "v24.22.1",
                 update_code: int = 0, update_stdout: str = APPLIED, update_stderr: str = "",
                 statuses: tuple[str, ...] = ("running",),
                 sessions: list[dict] | None = None, sessions_code: int = 0,
                 cron_jobs: list[dict] | None = None, cron_code: int = 0,
                 node_pid: int | None = SERVICE_PID, node_code: int = 0,
                 restart_failures: int = 0, stop_code: int = 0,
                 stop_stdout: str = "", stop_stderr: str = ""):
        self.machine = machine
        self.node = node
        self.update_code = update_code
        self.update_stdout = update_stdout
        self.update_stderr = update_stderr
        self.statuses = list(statuses)
        self.sessions = sessions or []
        self.sessions_code = sessions_code
        self.cron_jobs = cron_jobs or []
        self.cron_code = cron_code
        self.node_pid = node_pid
        self.node_code = node_code
        self.restart_failures = restart_failures
        self.stop_code = stop_code
        self.stop_stdout = stop_stdout
        self.stop_stderr = stop_stderr
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], env=None) -> RunResult:
        self.calls.append(list(argv))
        program = Path(argv[0]).name
        if program.startswith("npm") and argv[1:3] == ["root", "-g"]:
            return RunResult(0, str(self.machine.root), "")
        if program.startswith("node") and argv[1:] == ["--version"]:
            return RunResult(0, self.node, "")
        if program == "openclaw" and argv[1:2] == ["update"]:
            return RunResult(self.update_code, self.update_stdout, self.update_stderr)
        if program == "openclaw" and argv[1:3] == ["node", "stop"]:
            return RunResult(self.stop_code, self.stop_stdout, self.stop_stderr)
        if program == "openclaw" and argv[1:3] == ["node", "restart"]:
            if self.restart_failures:
                self.restart_failures -= 1
                return RunResult(1, "", BUSY)
            return RunResult(0, "", "")
        if program == "openclaw" and argv[1:3] == ["node", "status"]:
            if self.node_code:
                return RunResult(self.node_code, "", "the node service cannot be read")
            state = self.statuses.pop(0) if self.statuses else "running"
            if state is None:
                return RunResult(1, "", BUSY)
            return RunResult(0, json.dumps(_node_status(state, self.node_pid)), "")
        if program == "openclaw" and argv[1:3] == ["sessions", "list"]:
            if self.sessions_code:
                return RunResult(self.sessions_code, "", "the session store cannot be read")
            return RunResult(0, json.dumps({"sessions": self.sessions}), "")
        if program == "openclaw" and argv[1:3] == ["cron", "list"]:
            if self.cron_code:
                return RunResult(self.cron_code, "", "the gateway needs credentials")
            return RunResult(0, json.dumps({"jobs": self.cron_jobs}), "")
        return RunResult(0, "", "")


def _node_status(state: str, pid: int | None = SERVICE_PID) -> dict:
    """The shape `openclaw node status --json` reports, as `node_status` reads it."""
    runtime: dict = {"status": state}
    if pid is not None:
        runtime["pid"] = pid
    return {
        "service": {
            "loaded": True,
            "command": {"programArguments": ["openclaw", "node", "run", "--host", "gateway"]},
            "runtime": runtime,
        }
    }


def idle_node(_pid: int) -> bool:
    """A node service with no child process: nothing is running on it."""
    return False


def busy_node(_pid: int) -> bool:
    """A node service with a child of its own: a command an agent started is running."""
    return True


def a_proc_tree(root: Path, threads: dict[str, str]) -> Path:
    """A /proc-shaped tree: one service pid, one folder per thread, the kernel's child list."""
    for thread, children in threads.items():
        task = root / str(SERVICE_PID) / "task" / thread
        task.mkdir(parents=True)
        (task / "children").write_text(children, encoding="utf-8")
    return root


def lookup(machine: Machine):
    """`which` for the fake bin folder."""
    return lambda name: {"node": str(machine.bin / "node"), "npm": str(machine.bin / "npm"),
                         "openclaw": str(machine.bin / "openclaw")}.get(name)


def as_node(machine: Machine) -> None:
    """Record the role the engine writes when a machine is turned into a node."""
    role = machine.home / ".local" / "state" / "nexgen" / "role.json"
    role.parent.mkdir(parents=True)
    role.write_text(json.dumps({"role": "node"}), encoding="utf-8")


def as_gateway(machine: Machine) -> None:
    """Install the service file that makes this machine the gateway."""
    unit = machine.home / ".config" / "systemd" / "user" / "openclaw-gateway.service"
    unit.parent.mkdir(parents=True)
    unit.write_text("[Service]\nExecStart=openclaw gateway run\n", encoding="utf-8")


def updates(calls: list[list[str]]) -> list[list[str]]:
    """Only the `openclaw update` calls, in order."""
    return [call for call in calls if call[1:2] == ["update"]]


def restarts(calls: list[list[str]]) -> list[list[str]]:
    """Only the `openclaw node restart` calls, in order."""
    return [call for call in calls if call[1:3] == ["node", "restart"]]


def stops(calls: list[list[str]]) -> list[list[str]]:
    """Only the `openclaw node stop` calls, in order."""
    return [call for call in calls if call[1:3] == ["node", "stop"]]


# --------------------------------------------------------------- the decision


def test_a_machine_already_on_the_pin_is_left_alone(tmp_path: Path):
    machine = a_machine(tmp_path, version=PIN)
    fake = FakeRun(machine)

    outcome = version_align.align(machine.home, machine.engine, env={}, platform="linux",
                                 which=lookup(machine), run=fake)

    assert outcome.state == version_align.UNCHANGED
    assert updates(fake.calls) == []


def test_a_machine_behind_is_brought_onto_the_pin_by_the_native_updater(tmp_path: Path):
    machine = a_machine(tmp_path)
    as_node(machine)
    fake = FakeRun(machine)

    outcome = version_align.align(machine.home, machine.engine, env={}, platform="linux",
                                 which=lookup(machine), run=fake, clock=lambda: NIGHT,
                                 node_children=idle_node)

    assert outcome.state == version_align.ALIGNED
    assert updates(fake.calls) == [an_update(machine.bin / "openclaw", PIN)]


def test_the_node_floor_blocks_the_install_and_names_the_range(tmp_path: Path):
    machine = a_machine(tmp_path)
    as_node(machine)
    fake = FakeRun(machine, node="v22.1.0")

    outcome = version_align.align(machine.home, machine.engine, env={}, platform="linux",
                                 which=lookup(machine), run=fake)

    assert outcome.state == version_align.WARN
    assert NODE_RANGE in outcome.line
    assert NODE_RANGE in outcome.action
    assert updates(fake.calls) == []


def test_a_busy_gateway_waits_for_the_next_cycle(tmp_path: Path):
    machine = a_machine(tmp_path)
    as_gateway(machine)
    fake = FakeRun(machine, sessions=[{"key": "main:1", "status": "running"}])

    outcome = version_align.align(
        machine.home, machine.engine, env={}, platform="linux", which=lookup(machine), run=fake,
        smoke=lambda: 0, clock=lambda: NIGHT,
    )

    assert outcome.state == version_align.SKIPPED
    assert updates(fake.calls) == []


def test_a_gateway_is_verified_here_and_not_restarted_a_second_time(tmp_path: Path):
    """The native update restarts the gateway itself; the engine's part is the smoke test."""
    machine = a_machine(tmp_path)
    as_gateway(machine)
    fake = FakeRun(machine)
    smokes: list[int] = []

    outcome = version_align.align(
        machine.home, machine.engine, env={}, platform="linux", which=lookup(machine), run=fake,
        smoke=lambda: smokes.append(1) or 0, clock=lambda: NIGHT,
    )

    assert outcome.state == version_align.ALIGNED
    assert updates(fake.calls) == [an_update(machine.bin / "openclaw", PIN)]
    assert smokes == [1]
    assert restarts(fake.calls) == []


def test_the_gateway_smoke_runs_the_cli_that_owns_the_install(tmp_path: Path, monkeypatch):
    """A stale `openclaw` earlier on PATH must not be the one the smoke test judges."""
    from nexgen_core.openclaw import smoke as smoke_module

    machine = a_machine(tmp_path)
    as_gateway(machine)
    fake = FakeRun(machine)

    def a_smoke(*, run, out, budget_decides=True):
        run(["openclaw", "--version"], 1)
        return 0

    monkeypatch.setattr(smoke_module, "run_smoke", a_smoke)

    outcome = version_align.align(
        machine.home, machine.engine, env={}, platform="linux", which=lookup(machine), run=fake,
        clock=lambda: NIGHT,
    )

    assert outcome.state == version_align.ALIGNED
    assert [str(machine.bin / "openclaw"), "--version"] in fake.calls


def test_the_gate_does_not_let_a_token_count_roll_back_a_good_upgrade(tmp_path: Path, monkeypatch):
    """The release verdict is the functional checks alone: a large chat is not a broken release."""
    from nexgen_core.openclaw import smoke as smoke_module
    from nexgen_core.openclaw_service import RunResult

    asked: dict[str, bool] = {}

    def a_smoke(*, run=None, out=None, budget_decides=True):
        asked["budget_decides"] = budget_decides
        return 0

    monkeypatch.setattr(smoke_module, "run_smoke", a_smoke)
    fire = version_align._gateway_smoke(lambda argv, env=None: RunResult(0, "", ""), {}, None)

    assert fire() == 0
    assert asked["budget_decides"] is False


def test_a_gateway_that_does_not_come_back_puts_the_previous_release_back(tmp_path: Path):
    machine = a_machine(tmp_path)
    as_gateway(machine)
    fake = FakeRun(machine)

    outcome = version_align.align(
        machine.home, machine.engine, env={}, platform="linux", which=lookup(machine), run=fake,
        smoke=lambda: 1, clock=lambda: NIGHT,
    )

    assert outcome.state == version_align.FAILED
    assert updates(fake.calls) == [
        an_update(machine.bin / "openclaw", PIN),
        an_update(machine.bin / "openclaw", OLD),
    ]
    assert "was put back" in outcome.line
    remembered = json.loads((machine.state / version_align.STATE_FILE).read_text(encoding="utf-8"))
    assert remembered["pin"] == PIN


def test_a_release_with_no_known_predecessor_is_reported_and_not_rolled_back(tmp_path: Path):
    """A version nobody could read is not a target: the engine says so instead of guessing one."""
    machine = a_machine(tmp_path)
    as_gateway(machine)
    fake = FakeRun(machine)
    without_npm = {"node": str(machine.bin / "node"),
                   "openclaw": str(machine.bin / "openclaw")}.get

    outcome = version_align.align(
        machine.home, machine.engine, env={}, platform="linux", which=without_npm, run=fake,
        smoke=lambda: 1, clock=lambda: NIGHT,
    )

    assert outcome.state == version_align.FAILED
    assert updates(fake.calls) == [an_update(machine.bin / "openclaw", PIN)]
    assert "was put back" not in outcome.line
    assert (machine.state / version_align.STATE_FILE).is_file()


def test_a_node_that_does_not_report_running_is_put_back(tmp_path: Path):
    machine = a_machine(tmp_path)
    as_node(machine)
    fake = FakeRun(machine, statuses=("running", *["stopped"] * align_node.ATTEMPTS))

    outcome = version_align.align(machine.home, machine.engine, env={}, platform="linux",
                                 which=lookup(machine), run=fake, clock=lambda: NIGHT,
                                 node_children=idle_node)

    assert outcome.state == version_align.FAILED
    assert "2026.10.7" in outcome.line
    assert updates(fake.calls)[-1] == an_update(machine.bin / "openclaw", OLD)


def test_a_node_still_migrating_its_state_after_the_restart_is_waited_for(tmp_path: Path):
    """The first start of a release can answer "The CLI command failed." for a few seconds."""
    machine = a_machine(tmp_path)
    as_node(machine)
    fake = FakeRun(machine, statuses=("running", None, None, "running"))

    outcome = version_align.align(machine.home, machine.engine, env={}, platform="linux",
                                 which=lookup(machine), run=fake, clock=lambda: DAY,
                                 node_children=idle_node)

    assert outcome.state == version_align.ALIGNED
    assert updates(fake.calls) == [an_update(machine.bin / "openclaw", PIN)]


def test_a_node_service_is_restarted_after_the_native_update(tmp_path: Path):
    """The updater knows the gateway service; the node service that runs here is the engine's."""
    machine = a_machine(tmp_path)
    as_node(machine)
    fake = FakeRun(machine)

    outcome = version_align.align(machine.home, machine.engine, env={}, platform="linux",
                                 which=lookup(machine), run=fake, clock=lambda: DAY,
                                 node_children=idle_node)

    assert outcome.state == version_align.ALIGNED
    assert restarts(fake.calls) == [[str(machine.bin / "openclaw"), "node", "restart"]]
    assert stops(fake.calls) == []


def test_a_windows_node_is_stopped_before_the_install_and_started_again_after_it(tmp_path: Path):
    """npm cannot replace a native module the running node holds open: stop it around the install."""
    machine = a_machine(tmp_path)
    as_node(machine)
    fake = FakeRun(machine)

    outcome = version_align.align(machine.home, machine.engine, env={}, platform="win32",
                                 which=lookup(machine), run=fake, clock=lambda: DAY,
                                 node_children=idle_node)

    assert outcome.state == version_align.ALIGNED
    stop = fake.calls.index([str(machine.bin / "openclaw"), "node", "stop"])
    update = fake.calls.index(an_update(machine.bin / "openclaw", PIN))
    restart = fake.calls.index([str(machine.bin / "openclaw"), "node", "restart"])
    assert stop < update < restart


def test_a_windows_node_that_will_not_stop_is_reported_and_nothing_is_installed(tmp_path: Path):
    machine = a_machine(tmp_path)
    as_node(machine)
    fake = FakeRun(machine, stop_code=1, stop_stderr="Access is denied.")

    outcome = version_align.align(machine.home, machine.engine, env={}, platform="win32",
                                 which=lookup(machine), run=fake, clock=lambda: DAY,
                                 node_children=idle_node)

    assert outcome.state == version_align.FAILED
    assert "did not stop" in outcome.line
    assert "Access is denied." in outcome.line
    assert updates(fake.calls) == []
    assert not (machine.state / version_align.STATE_FILE).exists()


def test_a_windows_node_starts_again_when_the_install_fails(tmp_path: Path):
    """The service was stopped for the install: a failed install must not leave it down."""
    machine = a_machine(tmp_path)
    as_node(machine)
    fake = FakeRun(machine, update_code=1, update_stdout="", update_stderr="npm ERR! EPERM")

    outcome = version_align.align(machine.home, machine.engine, env={}, platform="win32",
                                 which=lookup(machine), run=fake, clock=lambda: DAY,
                                 node_children=idle_node)

    assert outcome.state == version_align.FAILED
    assert restarts(fake.calls) == [[str(machine.bin / "openclaw"), "node", "restart"]]


def test_a_node_restart_that_fails_once_is_asked_again(tmp_path: Path):
    machine = a_machine(tmp_path)
    as_node(machine)
    fake = FakeRun(machine, restart_failures=1)

    outcome = version_align.align(machine.home, machine.engine, env={}, platform="linux",
                                 which=lookup(machine), run=fake, clock=lambda: DAY,
                                 node_children=idle_node)

    assert outcome.state == version_align.ALIGNED
    assert updates(fake.calls) == [an_update(machine.bin / "openclaw", PIN)]


def test_a_node_restart_that_never_succeeds_is_put_back(tmp_path: Path):
    machine = a_machine(tmp_path)
    as_node(machine)
    fake = FakeRun(machine, restart_failures=align_node.ATTEMPTS)

    outcome = version_align.align(machine.home, machine.engine, env={}, platform="linux",
                                 which=lookup(machine), run=fake, clock=lambda: DAY,
                                 node_children=idle_node)

    assert outcome.state == version_align.FAILED
    assert updates(fake.calls)[-1] == an_update(machine.bin / "openclaw", OLD)


# ------------------------------------------------- what the updater itself answers


def test_a_failed_update_reports_the_updaters_own_reason(tmp_path: Path):
    """The engine does not paraphrase a failure it did not produce: the updater's words are the report."""
    machine = a_machine(tmp_path)
    as_node(machine)
    reported = json.dumps({
        "run": {
            "status": "failed",
            "failure": {"code": "target-registry-dist-tag", "message": "The registry dist-tag did not resolve"},
        }
    })
    fake = FakeRun(machine, update_code=1, update_stdout=reported, update_stderr="npm ERR! 404")

    outcome = version_align.align(machine.home, machine.engine, env={}, platform="linux",
                                 which=lookup(machine), run=fake, clock=lambda: NIGHT,
                                 node_children=idle_node)

    assert outcome.state == version_align.FAILED
    assert "The registry dist-tag did not resolve" in outcome.line
    assert "npm ERR! 404" not in outcome.line
    remembered = json.loads((machine.state / version_align.STATE_FILE).read_text(encoding="utf-8"))
    assert remembered["pin"] == PIN


def test_a_failed_update_with_no_json_is_reported_from_its_last_line(tmp_path: Path):
    machine = a_machine(tmp_path)
    as_node(machine)
    fake = FakeRun(machine, update_code=1, update_stdout="", update_stderr="npm ERR! boom")

    outcome = version_align.align(machine.home, machine.engine, env={}, platform="linux",
                                 which=lookup(machine), run=fake, clock=lambda: NIGHT,
                                 node_children=idle_node)

    assert outcome.state == version_align.FAILED
    assert "npm ERR! boom" in outcome.line
    assert updates(fake.calls) == [an_update(machine.bin / "openclaw", PIN)]


def test_a_generic_doctor_hint_does_not_hide_the_update_error(tmp_path: Path):
    """OpenClaw ends a failure with "For help, run `openclaw doctor`": that is not the reason."""
    machine = a_machine(tmp_path)
    as_node(machine)
    fake = FakeRun(
        machine, update_code=1, update_stdout="",
        update_stderr="npm ERR! EPERM: operation not permitted\nFor help, run `openclaw doctor`.",
    )

    outcome = version_align.align(machine.home, machine.engine, env={}, platform="linux",
                                 which=lookup(machine), run=fake, clock=lambda: NIGHT,
                                 node_children=idle_node)

    assert outcome.state == version_align.FAILED
    assert "EPERM" in outcome.line
    assert "For help" not in outcome.line


def test_tail_reports_the_last_meaningful_lines_instead_of_the_hint():
    result = RunResult(1, "", "first\nsecond\nthird\nfourth\nFor help, run `openclaw doctor`")

    assert align_node.tail(result) == "second third fourth"


def test_a_rolled_back_update_is_a_failure_even_when_it_exits_zero(tmp_path: Path):
    """The updater restores the previous release and says so; the engine must not call that aligned."""
    machine = a_machine(tmp_path)
    as_gateway(machine)
    reported = json.dumps({"run": {"status": "rolled-back",
                                  "failure": {"message": "post-install verify failed"}}})
    fake = FakeRun(machine, update_code=0, update_stdout=reported)

    outcome = version_align.align(
        machine.home, machine.engine, env={}, platform="linux", which=lookup(machine), run=fake,
        smoke=lambda: 0, clock=lambda: NIGHT,
    )

    assert outcome.state == version_align.FAILED
    assert "post-install verify failed" in outcome.line
    assert updates(fake.calls) == [an_update(machine.bin / "openclaw", PIN)]


def test_the_update_command_is_the_native_shape_with_and_without_a_resolved_binary():
    """One literal, asserted as the help page shows it: a discovered pin never goes through npm."""
    assert version_align._update_argv("/usr/bin/openclaw", "2026.10.7") == [
        "/usr/bin/openclaw", "update", "--tag", "2026.10.7", "--json", "--yes",
    ]
    assert version_align._update_argv(None, "2026.10.7") == [
        "openclaw", "update", "--tag", "2026.10.7", "--json", "--yes",
    ]


def test_a_failed_update_costs_no_smoke_turn_and_no_node_restart(tmp_path: Path):
    """Nothing changed on this machine, so nothing is woken up to prove it works."""
    machine = a_machine(tmp_path)
    as_gateway(machine)
    fake = FakeRun(machine, update_code=1, update_stdout="", update_stderr="npm ERR! boom")
    smokes: list[int] = []

    outcome = version_align.align(
        machine.home, machine.engine, env={}, platform="linux", which=lookup(machine), run=fake,
        smoke=lambda: smokes.append(1) or 0, clock=lambda: NIGHT,
    )

    assert outcome.state == version_align.FAILED
    assert smokes == []
    assert restarts(fake.calls) == []


def test_the_updaters_next_action_is_preferred_over_its_reason_code(tmp_path: Path):
    """A slug is not a sentence a person can act on: the updater's own next step is."""
    reported = json.dumps({"run": {
        "status": "failed",
        "reason": "target-registry-dist-tag",
        "origin": {"nextAction": "Check npm config get registry and retry."},
    }})
    machine = a_machine(tmp_path)
    as_node(machine)
    fake = FakeRun(machine, update_code=1, update_stdout=reported)

    outcome = version_align.align(machine.home, machine.engine, env={}, platform="linux",
                                 which=lookup(machine), run=fake, clock=lambda: NIGHT,
                                 node_children=idle_node)

    assert outcome.state == version_align.FAILED
    assert "Check npm config get registry and retry." in outcome.line
    assert "target-registry-dist-tag" not in outcome.line


def test_a_run_record_is_empty_when_the_answer_carries_none():
    """A shape this engine does not recognise costs a sentence, never an exception."""
    assert version_align._run_record("not json at all") == {}
    assert version_align._run_record("") == {}
    assert version_align._run_record(json.dumps(["a", "list"])) == {}
    assert version_align._run_record(json.dumps({"run": "a string"})) == {}
    assert version_align._run_record(json.dumps({"run": {"status": "succeeded"}})) == {"status": "succeeded"}


def test_a_pin_the_engine_cannot_read_is_a_warning_and_touches_nothing(tmp_path: Path):
    """No pin means no target: a guess would install a release nobody chose."""
    machine = a_machine(tmp_path)
    (machine.engine / "agent-universal-layer" / "openclaw" / "pin.json").write_text("{}", encoding="utf-8")
    fake = FakeRun(machine)

    outcome = version_align.align(machine.home, machine.engine, env={}, platform="linux",
                                 which=lookup(machine), run=fake, clock=lambda: NIGHT)

    assert outcome.state == version_align.WARN
    assert "pin.json" in outcome.action
    assert updates(fake.calls) == []


@pytest.mark.parametrize(("role", "expected"), [
    (version_align.GATEWAY, "OpenClaw 2026.10.7 is installed and the gateway answers."),
    (version_align.NODE, "OpenClaw 2026.10.7 is installed and the node service answers."),
    (version_align.NOTHING, "OpenClaw 2026.10.7 is installed on this machine."),
])
def test_the_success_line_names_what_answered(role: str, expected: str):
    """The sentence a person reads in the cycle log says which role was proven."""
    assert version_align._success_line(role, PIN) == expected


def test_a_held_pin_says_why_it_waits_and_costs_no_update(tmp_path: Path):
    """The hold is visible: a skipped cycle names the pin and the day, and asks nothing."""
    machine = a_machine(tmp_path)
    as_node(machine)
    machine.state.mkdir(parents=True, exist_ok=True)
    (machine.state / version_align.STATE_FILE).write_text(
        json.dumps({"pin": PIN, "failed_at": NIGHT - 60}), encoding="utf-8")
    fake = FakeRun(machine)

    outcome = version_align.align(machine.home, machine.engine, env={}, platform="linux",
                                 which=lookup(machine), run=fake, clock=lambda: NIGHT + 3_600,
                                 node_children=idle_node)

    assert outcome.state == version_align.SKIPPED
    assert PIN in outcome.line
    assert "24 hours" in outcome.line
    assert updates(fake.calls) == []


def test_a_refused_update_remembers_the_pin_and_keeps_the_cycle_green(tmp_path: Path):
    """A package-manager refusal the engine cannot fix is one warning, not a paged failure every cycle."""
    machine = a_machine(tmp_path)
    as_node(machine)
    reported = json.dumps({"run": {"status": "skipped", "reason": "container-image-install"}})
    fake = FakeRun(machine, update_code=0, update_stdout=reported)

    first = version_align.align(machine.home, machine.engine, env={}, platform="linux",
                                which=lookup(machine), run=fake, clock=lambda: NIGHT,
                                node_children=idle_node)
    second = version_align.align(machine.home, machine.engine, env={}, platform="linux",
                                 which=lookup(machine), run=fake, clock=lambda: NIGHT + 60,
                                 node_children=idle_node)

    assert first.state == version_align.WARN
    assert "container-image-install" in first.line
    assert json.loads((machine.state / version_align.STATE_FILE).read_text(encoding="utf-8"))["pin"] == PIN
    assert second.state == version_align.SKIPPED
    assert updates(fake.calls) == [an_update(machine.bin / "openclaw", PIN)]


def test_a_pin_that_later_arrives_clears_the_retry_marker(tmp_path: Path):
    """The 24-hour hold exists for a failed pin, not for the machine: a success drops it."""
    machine = a_machine(tmp_path)
    as_node(machine)
    fake = FakeRun(machine, update_code=1, update_stdout="", update_stderr="npm ERR! boom")
    version_align.align(machine.home, machine.engine, env={}, platform="linux",
                        which=lookup(machine), run=fake, clock=lambda: NIGHT,
                        node_children=idle_node)
    marker = machine.state / version_align.STATE_FILE
    assert marker.is_file()

    later = a_machine(tmp_path, version=PIN)
    outcome = version_align.align(later.home, later.engine, env={}, platform="linux",
                                 which=lookup(later), run=FakeRun(later), clock=lambda: NIGHT)

    assert outcome.state == version_align.UNCHANGED
    assert not marker.is_file()


def test_an_update_the_updater_refuses_to_own_is_a_warning_and_is_not_retried(tmp_path: Path):
    """A skipped run exits 0 and installs nothing: reporting it as aligned would hide a stale pin."""
    machine = a_machine(tmp_path)
    as_node(machine)
    reported = json.dumps({"run": {
        "status": "skipped",
        "reason": "unmanaged-package-install",
        "origin": {"nextAction": "No npm, pnpm, or Bun global owner was detected."},
    }})
    fake = FakeRun(machine, update_code=0, update_stdout=reported)

    outcome = version_align.align(machine.home, machine.engine, env={}, platform="linux",
                                 which=lookup(machine), run=fake, clock=lambda: NIGHT,
                                 node_children=idle_node)

    assert outcome.state == version_align.WARN
    assert "No npm, pnpm, or Bun global owner was detected." in outcome.line
    remembered = json.loads((machine.state / version_align.STATE_FILE).read_text(encoding="utf-8"))
    assert remembered["pin"] == PIN


def test_an_update_answer_that_is_not_json_is_taken_as_applied_when_it_exited_zero(tmp_path: Path):
    """An older CLI may print prose; a zero exit is then the only answer there is."""
    machine = a_machine(tmp_path)
    as_node(machine)
    fake = FakeRun(machine, update_code=0, update_stdout="OpenClaw updated to 2026.10.7")

    outcome = version_align.align(machine.home, machine.engine, env={}, platform="linux",
                                 which=lookup(machine), run=fake, clock=lambda: NIGHT,
                                 node_children=idle_node)

    assert outcome.state == version_align.ALIGNED


def test_the_same_failed_pin_is_not_retried_for_24_hours(tmp_path: Path):
    machine = a_machine(tmp_path)
    as_node(machine)
    machine.state.mkdir(parents=True, exist_ok=True)
    (machine.state / version_align.STATE_FILE).write_text(
        json.dumps({"pin": PIN, "failed_at": NIGHT - 60}), encoding="utf-8")
    fake = FakeRun(machine)

    early = version_align.align(machine.home, machine.engine, env={}, platform="linux",
                                which=lookup(machine), run=fake, clock=lambda: NIGHT + 3_600,
                                node_children=idle_node)
    late = version_align.align(machine.home, machine.engine, env={}, platform="linux",
                               which=lookup(machine), run=fake,
                               clock=lambda: NIGHT + version_align.RETRY_SECONDS + 1,
                               node_children=idle_node)

    assert early.state == version_align.SKIPPED
    assert updates(fake.calls) == [an_update(machine.bin / "openclaw", PIN)]
    assert late.state == version_align.ALIGNED


def test_a_different_pin_is_tried_at_once(tmp_path: Path):
    machine = a_machine(tmp_path)
    as_node(machine)
    machine.state.mkdir(parents=True, exist_ok=True)
    (machine.state / version_align.STATE_FILE).write_text(
        json.dumps({"pin": "2026.9.1", "failed_at": NIGHT - 60}), encoding="utf-8")
    fake = FakeRun(machine)

    outcome = version_align.align(machine.home, machine.engine, env={}, platform="linux",
                                 which=lookup(machine), run=fake, clock=lambda: NIGHT,
                                 node_children=idle_node)

    assert outcome.state == version_align.ALIGNED


def test_an_unexpected_error_is_an_outcome_and_never_a_raise(tmp_path: Path):
    machine = a_machine(tmp_path)
    as_gateway(machine)
    fake = FakeRun(machine)

    def exploding_smoke() -> int:
        raise RuntimeError("the smoke test broke")

    outcome = version_align.align(
        machine.home, machine.engine, env={}, platform="linux", which=lookup(machine), run=fake,
        smoke=exploding_smoke, clock=lambda: NIGHT,
    )

    assert outcome.state == version_align.FAILED
    assert "the smoke test broke" in outcome.line


def test_openclaw_that_is_not_installed_is_a_quiet_no_op(tmp_path: Path):
    machine = a_machine(tmp_path)
    fake = FakeRun(machine)

    outcome = version_align.align(machine.home, machine.engine, env={}, platform="linux",
                                 which=lambda _name: None, run=fake)

    assert outcome.state == version_align.UNCHANGED
    assert fake.calls == []


def test_a_home_with_no_configuration_starts_nothing(tmp_path: Path):
    """The npm package being on PATH is not evidence that OpenClaw runs in THIS home."""
    machine = a_machine(tmp_path)
    (machine.home / ".openclaw" / "openclaw.json").unlink()
    fake = FakeRun(machine)

    outcome = version_align.align(machine.home, machine.engine, env={}, platform="linux",
                                 which=lookup(machine), run=fake)

    assert outcome.state == version_align.UNCHANGED
    assert fake.calls == []


def test_host_mutations_disabled_leaves_the_machine_untouched(tmp_path: Path, monkeypatch):
    machine = a_machine(tmp_path)
    monkeypatch.setenv("NEXGEN_DISABLE_HOST_MUTATIONS", "1")
    fake = FakeRun(machine)

    outcome = version_align.align(machine.home, machine.engine, env={}, platform="linux",
                                 which=lookup(machine), run=fake)

    assert outcome.state == version_align.UNCHANGED
    assert fake.calls == []


# ------------------------------------------------ when the cycle may touch anything


@pytest.mark.parametrize(("hour", "may_go"), [(2, False), (3, True), (5, True), (6, False)])
def test_the_window_is_three_to_six_local_hours(hour: int, may_go: bool):
    """03:00 is inside the window and 06:00 is not, and the window belongs to the gateway alone."""
    moment = datetime(2026, 10, 30, hour, 0).timestamp()

    def run(argv: list[str], _env=None) -> RunResult:
        if argv[1:3] == ["cron", "list"]:
            return RunResult(0, json.dumps({"jobs": []}), "")
        return RunResult(0, json.dumps({"sessions": []}), "")

    sentence = align_window.postponement(
        runs_gateway=True, runs_node=False, moment=moment, binary="/usr/bin/openclaw",
        env={}, run=run,
    )

    assert (sentence is None) is may_go


def test_outside_the_night_window_the_gateway_is_left_alone(tmp_path: Path):
    machine = a_machine(tmp_path)
    as_gateway(machine)
    fake = FakeRun(machine)

    outcome = version_align.align(
        machine.home, machine.engine, env={}, platform="linux", which=lookup(machine), run=fake,
        smoke=lambda: 0, clock=lambda: DAY,
    )

    assert outcome.state == version_align.SKIPPED
    assert updates(fake.calls) == []


def test_inside_the_night_window_an_idle_gateway_aligns(tmp_path: Path):
    machine = a_machine(tmp_path)
    as_gateway(machine)
    fake = FakeRun(machine)

    outcome = version_align.align(
        machine.home, machine.engine, env={}, platform="linux", which=lookup(machine), run=fake,
        smoke=lambda: 0, clock=lambda: NIGHT,
    )

    assert outcome.state == version_align.ALIGNED
    assert updates(fake.calls) == [an_update(machine.bin / "openclaw", PIN)]


def test_a_running_automation_postpones_the_install(tmp_path: Path):
    machine = a_machine(tmp_path)
    as_gateway(machine)
    fake = FakeRun(machine, cron_jobs=[{"id": "morning-brief", "enabled": True, "status": "running"}])

    outcome = version_align.align(
        machine.home, machine.engine, env={}, platform="linux", which=lookup(machine), run=fake,
        smoke=lambda: 0, clock=lambda: NIGHT,
    )

    assert outcome.state == version_align.SKIPPED
    assert updates(fake.calls) == []


def test_an_automation_that_recorded_a_start_time_but_no_status_postpones_too(tmp_path: Path):
    """Older payloads carry only `state.runningAtMs`, and a job that is executing must still be seen."""
    machine = a_machine(tmp_path)
    as_gateway(machine)
    fake = FakeRun(machine, cron_jobs=[{"id": "nightly", "enabled": True, "state": {"runningAtMs": 1}}])

    outcome = version_align.align(
        machine.home, machine.engine, env={}, platform="linux", which=lookup(machine), run=fake,
        smoke=lambda: 0, clock=lambda: NIGHT,
    )

    assert outcome.state == version_align.SKIPPED
    assert updates(fake.calls) == []


def test_an_unreadable_session_list_is_never_taken_for_idle(tmp_path: Path):
    """Fail closed: a state that cannot be read is not a machine that is free."""
    machine = a_machine(tmp_path)
    as_gateway(machine)
    fake = FakeRun(machine, sessions_code=1)

    outcome = version_align.align(
        machine.home, machine.engine, env={}, platform="linux", which=lookup(machine), run=fake,
        smoke=lambda: 0, clock=lambda: NIGHT,
    )

    assert outcome.state == version_align.SKIPPED
    assert updates(fake.calls) == []


def test_a_session_list_that_is_not_json_is_never_taken_for_idle(tmp_path: Path):
    machine = a_machine(tmp_path)
    as_gateway(machine)
    fake = FakeRun(machine)

    def list_sessions(_binary: str):
        return None

    outcome = version_align.align(
        machine.home, machine.engine, env={}, platform="linux", which=lookup(machine), run=fake,
        smoke=lambda: 0, clock=lambda: NIGHT, list_sessions=list_sessions,
    )

    assert outcome.state == version_align.SKIPPED
    assert updates(fake.calls) == []


def test_a_session_lister_that_raises_postpones_instead_of_failing_the_phase(tmp_path: Path):
    machine = a_machine(tmp_path)
    as_gateway(machine)
    fake = FakeRun(machine)

    def list_sessions(_binary: str):
        raise OSError("the CLI is gone")

    outcome = version_align.align(
        machine.home, machine.engine, env={}, platform="linux", which=lookup(machine), run=fake,
        smoke=lambda: 0, clock=lambda: NIGHT, list_sessions=list_sessions,
    )

    assert outcome.state == version_align.SKIPPED
    assert updates(fake.calls) == []


def test_an_unreadable_cron_list_on_the_gateway_is_never_taken_for_idle(tmp_path: Path):
    machine = a_machine(tmp_path)
    as_gateway(machine)
    fake = FakeRun(machine, cron_code=1)

    outcome = version_align.align(
        machine.home, machine.engine, env={}, platform="linux", which=lookup(machine), run=fake,
        smoke=lambda: 0, clock=lambda: NIGHT,
    )

    assert outcome.state == version_align.SKIPPED
    assert updates(fake.calls) == []


def test_a_node_aligns_in_the_middle_of_the_day_when_no_command_runs_on_it(tmp_path: Path):
    """A node waits for no window: a PC that is off at night would never update."""
    machine = a_machine(tmp_path)
    as_node(machine)
    fake = FakeRun(machine)

    outcome = version_align.align(machine.home, machine.engine, env={}, platform="linux",
                                 which=lookup(machine), run=fake, clock=lambda: DAY,
                                 node_children=idle_node)

    assert outcome.state == version_align.ALIGNED
    assert updates(fake.calls) == [an_update(machine.bin / "openclaw", PIN)]
    # The session list is the gateway's signal: a node never reads it.
    assert not any(call[1:3] == ["sessions", "list"] for call in fake.calls)


def test_a_node_running_a_command_waits(tmp_path: Path):
    """The command an agent started on the node is a child of its service: that is the job."""
    machine = a_machine(tmp_path)
    as_node(machine)
    fake = FakeRun(machine)

    outcome = version_align.align(machine.home, machine.engine, env={}, platform="linux",
                                 which=lookup(machine), run=fake, clock=lambda: DAY,
                                 node_children=busy_node)

    assert outcome.state == version_align.SKIPPED
    assert "a command is running on this node" in outcome.line
    assert updates(fake.calls) == []


def test_a_node_whose_children_cannot_be_read_waits(tmp_path: Path):
    """Fail closed: a pid that cannot be watched is not a machine that is free."""
    machine = a_machine(tmp_path)
    as_node(machine)

    def blind(_pid: int) -> bool | None:
        return None

    outcome = version_align.align(machine.home, machine.engine, env={}, platform="linux",
                                 which=lookup(machine), run=FakeRun(machine), clock=lambda: DAY,
                                 node_children=blind)

    assert outcome.state == version_align.SKIPPED
    assert "the work state cannot be read" in outcome.line


def test_a_children_reader_that_raises_postpones_instead_of_failing_the_phase(tmp_path: Path):
    machine = a_machine(tmp_path)
    as_node(machine)
    fake = FakeRun(machine)

    def exploding(_pid: int) -> bool:
        raise OSError("the kernel said no")

    outcome = version_align.align(machine.home, machine.engine, env={}, platform="linux",
                                 which=lookup(machine), run=fake, clock=lambda: DAY,
                                 node_children=exploding)

    assert outcome.state == version_align.SKIPPED
    assert updates(fake.calls) == []


def test_a_node_that_reports_no_pid_is_never_taken_for_idle(tmp_path: Path):
    """No pid means no way to see a command in flight, and an unprovable idle waits."""
    machine = a_machine(tmp_path)
    as_node(machine)
    fake = FakeRun(machine, node_pid=None)

    outcome = version_align.align(machine.home, machine.engine, env={}, platform="linux",
                                 which=lookup(machine), run=fake, clock=lambda: DAY,
                                 node_children=idle_node)

    assert outcome.state == version_align.SKIPPED
    assert "the work state cannot be read" in outcome.line
    assert updates(fake.calls) == []


def test_a_node_service_that_is_not_running_is_never_taken_for_idle(tmp_path: Path):
    machine = a_machine(tmp_path)
    as_node(machine)
    fake = FakeRun(machine, statuses=("stopped",))

    outcome = version_align.align(machine.home, machine.engine, env={}, platform="linux",
                                 which=lookup(machine), run=fake, clock=lambda: DAY,
                                 node_children=idle_node)

    assert outcome.state == version_align.SKIPPED
    assert "the work state cannot be read" in outcome.line
    assert updates(fake.calls) == []


def test_a_node_status_that_cannot_be_read_is_never_taken_for_idle(tmp_path: Path):
    machine = a_machine(tmp_path)
    as_node(machine)
    fake = FakeRun(machine, node_code=1)

    outcome = version_align.align(machine.home, machine.engine, env={}, platform="linux",
                                 which=lookup(machine), run=fake, clock=lambda: DAY,
                                 node_children=idle_node)

    assert outcome.state == version_align.SKIPPED
    assert "the work state cannot be read" in outcome.line
    assert updates(fake.calls) == []


def test_a_node_reads_no_cron_list_because_the_scheduler_runs_on_the_gateway(tmp_path: Path):
    machine = a_machine(tmp_path)
    as_node(machine)
    fake = FakeRun(machine, cron_code=1)

    outcome = version_align.align(machine.home, machine.engine, env={}, platform="linux",
                                 which=lookup(machine), run=fake, clock=lambda: NIGHT,
                                 node_children=idle_node)

    assert outcome.state == version_align.ALIGNED
    assert not any(call[1:3] == ["cron", "list"] for call in fake.calls)


def test_a_package_with_no_service_here_waits_for_nothing(tmp_path: Path):
    """Nothing runs on this machine, so there is no job to interrupt and no state to read."""
    machine = a_machine(tmp_path)
    fake = FakeRun(machine)

    outcome = version_align.align(machine.home, machine.engine, env={}, platform="linux",
                                 which=lookup(machine), run=fake, clock=lambda: DAY)

    assert outcome.state == version_align.ALIGNED


# ------------------------------------------------------- reading a pid's children


def test_the_linux_reader_sees_no_child_on_an_idle_service(tmp_path: Path):
    proc = a_proc_tree(tmp_path, {str(SERVICE_PID): ""})

    assert align_window.children_present(SERVICE_PID, platform="linux", proc=proc) is False


def test_the_linux_reader_sees_the_child_a_command_runs_as(tmp_path: Path):
    """Any thread of the service holding a child is a command in flight."""
    proc = a_proc_tree(tmp_path, {str(SERVICE_PID): "", "474702": "475102\n"})

    assert align_window.children_present(SERVICE_PID, platform="linux", proc=proc) is True


def test_a_proc_entry_that_is_gone_is_an_unreadable_state(tmp_path: Path):
    assert align_window.children_present(SERVICE_PID, platform="linux", proc=tmp_path) is None


def test_a_children_file_that_cannot_be_read_is_an_unreadable_state(tmp_path: Path):
    proc = a_proc_tree(tmp_path, {str(SERVICE_PID): "475102\n"})
    (proc / str(SERVICE_PID) / "task" / str(SERVICE_PID) / "children").unlink()
    (proc / str(SERVICE_PID) / "task" / str(SERVICE_PID) / "children").mkdir()

    assert align_window.children_present(SERVICE_PID, platform="linux", proc=proc) is None


def test_the_macos_reader_reads_the_pgrep_exit_code():
    """`pgrep -P` exits 1 when the process has no children and 0 when it has some."""

    def asked(code: int):
        return lambda _argv, _env=None: RunResult(code, "", "")

    assert align_window.children_present(SERVICE_PID, platform="darwin", run=asked(1)) is False
    assert align_window.children_present(SERVICE_PID, platform="darwin", run=asked(0)) is True
    assert align_window.children_present(SERVICE_PID, platform="darwin", run=asked(2)) is None


def test_the_windows_reader_asks_the_process_table_for_a_count():
    calls: list[list[str]] = []

    def asked(_argv: list[str], _env=None) -> RunResult:
        calls.append(list(_argv))
        return RunResult(0, "2\n", "")

    assert align_window.children_present(SERVICE_PID, platform="win32", run=asked) is True
    assert calls[0][1:3] == ["-NoProfile", "-Command"]
    assert f"ParentProcessId={SERVICE_PID}" in calls[0][-1]


def test_a_platform_with_no_reader_is_never_taken_for_idle():
    assert align_window.children_present(SERVICE_PID, platform="aix") is None


def test_each_postpone_says_which_reason_it_was(tmp_path: Path):
    """A short reason is what lets a person read the cycle log: the reasons must differ."""
    machine = a_machine(tmp_path)
    as_gateway(machine)

    def run_once(*, fake: FakeRun, moment: float):
        return version_align.align(
            machine.home, machine.engine, env={}, platform="linux", which=lookup(machine),
            run=fake, smoke=lambda: 0, clock=lambda: moment,
        )

    day = run_once(fake=FakeRun(machine), moment=DAY)
    busy = run_once(fake=FakeRun(machine, sessions=[{"key": "main:1", "status": "active"}]), moment=NIGHT)
    cron = run_once(fake=FakeRun(machine, cron_jobs=[{"status": "running"}]), moment=NIGHT)
    blind = run_once(fake=FakeRun(machine, sessions_code=1), moment=NIGHT)

    assert {day.state, busy.state, cron.state, blind.state} == {version_align.SKIPPED}
    lines = {day.line, busy.line, cron.line, blind.line}
    assert len(lines) == 4


# ---------------------------------------------------------------- the guard


def _guard_runner(tmp_path: Path, monkeypatch):
    """A runner whose other phases only record that they ran."""
    from nexgen_core.guard import GuardRunner

    vault = tmp_path / "vault"
    vault.mkdir()
    runner = GuardRunner(vault_data=vault, home=tmp_path / "home")
    monkeypatch.setattr(runner, "_phase_git", lambda *_a: None)
    monkeypatch.setattr(runner, "_phase_preflight", lambda *_a: None)
    for name in ("skills", "mcp", "permissions", "instructions", "launchers",
                 "scheduler", "modules", "runtime"):
        monkeypatch.setattr(runner, f"_phase_{name}", lambda *_a: None)
    return runner


def test_a_failed_alignment_fails_the_phase_not_the_other_phases(tmp_path: Path, monkeypatch):
    from nexgen_core.guard import GuardMode

    runner = _guard_runner(tmp_path, monkeypatch)
    monkeypatch.setattr(version_align, "align", lambda **_kw: version_align.Outcome(
        version_align.FAILED, "OpenClaw did not work here"))

    result = runner.run(GuardMode.APPLY)

    assert result.success is False and result.exit_code == 1
    assert runner.heartbeat.recorded_failed_phases() == ["openclaw"]


def test_a_machine_merely_behind_does_not_fail_the_cycle(tmp_path: Path, monkeypatch):
    from nexgen_core.guard import GuardMode

    runner = _guard_runner(tmp_path, monkeypatch)
    monkeypatch.setattr(version_align, "align", lambda **_kw: version_align.Outcome(
        version_align.ALIGNED, "OpenClaw is now on the pin"))

    result = runner.run(GuardMode.APPLY)

    assert result.success is True and result.exit_code == 0
    assert runner.heartbeat.recorded_failed_phases() == []
    assert "OpenClaw is now on the pin" in result.actions_taken


def test_a_node_floor_warning_is_counted_as_a_warning(tmp_path: Path, monkeypatch):
    from nexgen_core import action_notes
    from nexgen_core.guard import GuardMode

    runner = _guard_runner(tmp_path, monkeypatch)
    monkeypatch.setattr(version_align, "align", lambda **_kw: version_align.Outcome(
        version_align.WARN, "Node is too old", "install a newer Node"))

    result = runner.run(GuardMode.APPLY)

    assert result.success is True
    assert runner.heartbeat.recorded_warnings() == 1
    assert any(action_notes.is_warning(a) and "install a newer Node" in a for a in result.actions_taken)


def test_a_postponed_cycle_is_neither_a_failure_nor_a_warning(tmp_path: Path, monkeypatch):
    """A machine waiting for its window happens every day: it must not page anybody."""
    from nexgen_core import action_notes
    from nexgen_core.guard import GuardMode

    runner = _guard_runner(tmp_path, monkeypatch)
    monkeypatch.setattr(version_align, "align", lambda **_kw: version_align.Outcome(
        version_align.SKIPPED, "Rimandato alla finestra notturna."))

    result = runner.run(GuardMode.APPLY)

    assert result.success is True and result.exit_code == 0
    assert runner.heartbeat.recorded_failed_phases() == []
    assert runner.heartbeat.recorded_warnings() == 0
    assert not any(action_notes.is_warning(a) for a in result.actions_taken)
    assert any("finestra notturna" in a for a in result.actions_taken)


class EnvRun(FakeRun):
    """Records the PATH every program was started with."""

    def __init__(self, machine: Machine, **kwargs):
        super().__init__(machine, **kwargs)
        self.paths: list[str] = []

    def __call__(self, argv: list[str], env=None) -> RunResult:
        self.paths.append((env or {}).get("PATH", ""))
        return super().__call__(argv, env)


def test_the_cli_runs_under_the_node_that_owns_openclaw(tmp_path: Path):
    """Seen on a desktop: `~/.local/bin/node` linked to an older Node came first on PATH, and
    `openclaw` starts with `#!/usr/bin/env node`, so the wrong Node ran this release."""
    machine = a_machine(tmp_path)
    as_node(machine)
    stale = tmp_path / "stale-local-bin"
    stale.mkdir()
    fake = EnvRun(machine)

    version_align.align(
        machine.home, machine.engine, env={"PATH": os.pathsep.join([str(stale), "/usr/bin"])}, platform="linux",
        which=lookup(machine), run=fake, clock=lambda: DAY, node_children=idle_node,
    )

    assert fake.paths
    assert all(path.split(os.pathsep)[0] == str(machine.bin) for path in fake.paths)
