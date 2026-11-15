"""Tests for OpenClaw gateway service and pairing management.

The service is OpenClaw's own (`openclaw gateway install/start/stop/restart/
status/uninstall`); these tests pin the engine's half of it with injected
runners, process listers and port probes. No real system process, port or file
under ~ is touched.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.openclaw_service import (  # noqa: E402
    OpenClawService,
    ProcessInfo,
    RunResult,
    is_gateway_process,
    removal_commands,
    install as service_install,
    restart as service_restart,
    start as service_start,
    status as service_status,
    stop as service_stop,
    stopped as service_stopped,
    uninstall as service_uninstall,
)
from nexgen_core.openclaw_pairing import (  # noqa: E402
    approve as pairing_approve,
    listed as pairing_listed,
    pending as pairing_pending,
    qr as pairing_qr,
)

STATUS_ARGV = ["openclaw", "gateway", "status", "--json"]
DEEP_STATUS_ARGV = ["openclaw", "gateway", "status", "--deep", "--json"]
INSTALL_ARGV = ["openclaw", "gateway", "install", "--json"]
START_ARGV = ["openclaw", "gateway", "start", "--json"]
STOP_ARGV = ["openclaw", "gateway", "stop", "--json", "--force"]
RESTART_ARGV = ["openclaw", "gateway", "restart", "--json"]
UNINSTALL_ARGV = ["openclaw", "gateway", "uninstall", "--json"]


class RecordingRunner:
    """Fake command runner that records invocations and returns canned responses."""

    def __init__(self, default_code: int = 0, default_stdout: str = "", default_stderr: str = ""):
        self.calls: list[list[str]] = []
        self.default_code = default_code
        self.default_stdout = default_stdout
        self.default_stderr = default_stderr
        self.canned: dict[tuple[str, ...], RunResult] = {}

    def set_canned(self, argv: list[str], code: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.canned[tuple(argv)] = RunResult(code, stdout, stderr)

    def answer(self, argv: list[str], payload: dict, code: int = 0) -> None:
        self.set_canned(argv, code=code, stdout=json.dumps(payload))

    def __call__(self, argv: list[str]) -> RunResult:
        self.calls.append(list(argv))
        key = tuple(argv)
        if key in self.canned:
            return self.canned[key]
        return RunResult(self.default_code, self.default_stdout, self.default_stderr)


class FakeClock:
    """Fake monotonic clock and sleeper for testing timeouts deterministically."""

    def __init__(self, start: float = 0.0):
        self.current_time = start
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.current_time

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.current_time += seconds


@pytest.fixture
def fake_env(tmp_path: Path):
    """Isolated environment paths avoiding any real system or home paths."""
    home = tmp_path / "home"
    state_dir = home / ".openclaw"
    state_dir.mkdir(parents=True)
    appdata = tmp_path / "appdata" / "Roaming"
    startup = appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    startup.mkdir(parents=True)
    localappdata = tmp_path / "appdata" / "Local"
    packages = localappdata / "Packages"
    packages.mkdir(parents=True)

    return {
        "home": home,
        "state_dir": state_dir,
        "appdata": appdata,
        "startup": startup,
        "localappdata": localappdata,
        "packages": packages,
    }


def service_for(fake_env, runner: RecordingRunner, platform: str = "win32", **kwargs) -> OpenClawService:
    """A service controller over the isolated paths, with the runner given."""
    return OpenClawService(
        platform=platform,
        home=fake_env["home"],
        appdata=fake_env["appdata"],
        localappdata=fake_env["localappdata"],
        runner=runner,
        **kwargs,
    )


def registered_service(label: str = "Scheduled Task", loaded: bool = True) -> dict:
    """The `service` block `openclaw gateway status --json` prints (2026.11.2)."""
    return {
        "service": {
            "label": label,
            "loaded": loaded,
            "loadState": {"status": "loaded" if loaded else "not-loaded"},
            "loadedText": "registered",
            "notLoadedText": "missing",
            "runtime": {"status": "running" if loaded else "stopped", "missingUnit": not loaded},
        }
    }


# --------------------------------------------------------------------------- status


def test_status_reads_the_service_openclaw_registers(fake_env) -> None:
    """`installed` is OpenClaw's own answer, and its label is the mechanism."""
    runner = RecordingRunner()
    runner.answer(STATUS_ARGV, registered_service("Scheduled Task"))
    processes = [
        ProcessInfo(101, "node.exe", r"node.exe C:\tools\openclaw\dist\index.js gateway --port 18789"),
    ]

    service = service_for(
        fake_env, runner,
        process_lister=lambda: processes,
        port_probe=lambda port, host="127.0.0.1": True,
    )

    st = service.status()

    assert st["installed"] is True
    assert st["mechanism"] == "scheduled task"
    assert st["process_count"] == 1
    assert st["port_listening"] is True
    assert st["companion_present"] is False
    assert st["dual_gateway"] is False


def test_status_reports_the_systemd_unit_label_openclaw_prints(fake_env) -> None:
    runner = RecordingRunner()
    runner.answer(STATUS_ARGV, registered_service("systemd user"))

    st = service_for(fake_env, runner, platform="linux", process_lister=lambda: []).status()

    assert st["installed"] is True
    assert st["mechanism"] == "systemd user"


def test_status_reports_nothing_installed_when_no_service_is_registered(fake_env) -> None:
    """A missing unit is `loaded: false`, which is not the same as a stopped one."""
    runner = RecordingRunner()
    runner.answer(STATUS_ARGV, registered_service(loaded=False))

    st = service_for(fake_env, runner, process_lister=lambda: []).status()

    assert st["installed"] is False
    assert st["mechanism"] is None
    assert st["process_count"] == 0


def test_status_asks_openclaw_and_never_a_service_manager_of_its_own(fake_env) -> None:
    """The engine used to read the Startup folder, `schtasks` and unit files itself."""
    runner = RecordingRunner()
    runner.answer(STATUS_ARGV, registered_service())

    service_for(fake_env, runner, process_lister=lambda: []).status()

    assert runner.calls == [STATUS_ARGV]
    assert not any(call[0] in {"schtasks.exe", "systemctl"} for call in runner.calls)


def test_status_of_a_cli_that_printed_no_json_says_nothing_is_installed(fake_env) -> None:
    """An older CLI, or an error before the service is read, is not a crash."""
    runner = RecordingRunner(default_code=1)

    st = service_for(fake_env, runner, process_lister=lambda: []).status()

    assert st["installed"] is False
    assert st["mechanism"] is None


def test_status_dry_run_runs_nothing(fake_env) -> None:
    runner = RecordingRunner()

    st = service_for(fake_env, runner, process_lister=lambda: []).status(dry_run=True)

    assert runner.calls == []
    assert st["installed"] is False
    assert st["dry_run"] is True


def test_status_dual_gateway_processes(fake_env) -> None:
    runner = RecordingRunner()
    runner.answer(STATUS_ARGV, registered_service())
    processes = [
        ProcessInfo(301, "node.exe", r"node C:\openclaw\dist\index.js gateway"),
        ProcessInfo(302, "node.exe", r"node C:\openclaw\dist\index.js gateway"),
    ]

    st = service_for(fake_env, runner, process_lister=lambda: processes,
                     port_probe=lambda port, host="127.0.0.1": True).status()

    assert st["process_count"] == 2
    assert st["dual_gateway"] is True


def test_status_detects_microsoft_store_companion(fake_env) -> None:
    comp_pkg = fake_env["packages"] / "OpenClaw.CompanionApp_8wekyb3d8bbwe"
    comp_pkg.mkdir()
    comp_lnk = fake_env["startup"] / "OpenClaw Companion.lnk"
    comp_lnk.write_text("fake shortcut", encoding="utf-8")
    runner = RecordingRunner()
    runner.answer(STATUS_ARGV, registered_service())

    st = service_for(fake_env, runner, process_lister=lambda: []).status()

    assert st["companion_present"] is True
    assert "Remove-AppxPackage" in (st["companion_removal_command"] or "")


# ----------------------------------------------------------------- lifecycle verbs


def test_install_runs_the_openclaw_install_and_reads_the_service_back(fake_env) -> None:
    runner = RecordingRunner()
    runner.answer(INSTALL_ARGV, {"ok": True, "action": "install", "result": "installed", "message": "ok"})
    runner.answer(STATUS_ARGV, registered_service())

    res = service_for(fake_env, runner, process_lister=lambda: []).install()

    assert res["ok"] is True
    assert res["commands"] == [INSTALL_ARGV]
    assert res["status"]["installed"] is True
    assert runner.calls == [INSTALL_ARGV, STATUS_ARGV]


def test_install_reports_the_failure_openclaw_printed(fake_env) -> None:
    runner = RecordingRunner()
    runner.answer(INSTALL_ARGV, {"ok": False, "action": "install", "error": "Gateway install failed: access denied"},
                  code=1)
    runner.answer(STATUS_ARGV, registered_service(loaded=False))

    res = service_for(fake_env, runner, process_lister=lambda: []).install()

    assert res["ok"] is False
    assert res["error"] == "Gateway install failed: access denied"


def test_start_runs_the_service_start_and_reports_success(fake_env) -> None:
    runner = RecordingRunner()
    runner.answer(START_ARGV, {"ok": True, "action": "start", "result": "started", "message": "started"})

    res = service_for(fake_env, runner, process_lister=lambda: []).start()

    assert res["ok"] is True
    assert res["commands"] == [START_ARGV]
    assert runner.calls == [START_ARGV]


def test_start_treats_still_starting_as_a_service_that_is_up(fake_env) -> None:
    """Exit code 2 is `still-starting`: the service runs and the port is warming up."""
    runner = RecordingRunner()
    runner.answer(START_ARGV, {"ok": False, "action": "start", "error": "Gateway start timed out",
                               "result": "still-starting"}, code=2)

    res = service_for(fake_env, runner, process_lister=lambda: []).start()

    assert res["ok"] is True
    assert res["error"] == "Gateway start timed out"


def test_start_reports_a_refusal_as_a_failure(fake_env) -> None:
    runner = RecordingRunner()
    runner.answer(START_ARGV, {"ok": False, "action": "start", "error": "Gateway start blocked: config is invalid"},
                  code=1)

    res = service_for(fake_env, runner, process_lister=lambda: []).start()

    assert res["ok"] is False
    assert res["error"] == "Gateway start blocked: config is invalid"


def test_stop_asks_openclaw_with_the_consent_a_non_interactive_shell_needs(fake_env) -> None:
    runner = RecordingRunner()
    runner.answer(STOP_ARGV, {"ok": True, "action": "stop", "result": "stopped"})

    res = service_for(fake_env, runner, process_lister=lambda: []).stop()

    assert res["ok"] is True
    assert res["commands"] == [STOP_ARGV]
    assert res["remaining_pids"] == []
    assert runner.calls == [STOP_ARGV]


def test_stop_names_the_pids_still_alive_when_openclaw_could_not_stop_it(fake_env) -> None:
    """`nexgen openclaw stop` prints those pids, so they have to be the live ones."""
    runner = RecordingRunner()
    runner.answer(STOP_ARGV, {"ok": False, "action": "stop", "error": "Gateway stop failed: task did not exit"},
                  code=1)
    procs = [ProcessInfo(401, "node.exe", r"node C:\openclaw\dist\index.js gateway --port 18789")]

    res = service_for(fake_env, runner, process_lister=lambda: procs).stop()

    assert res["ok"] is False
    assert res["remaining_pids"] == [401]
    assert res["error"] == "Gateway stop failed: task did not exit"


def test_stop_never_kills_a_process_itself(fake_env) -> None:
    """`taskkill` and `kill` are OpenClaw's, with its PID and start-time check."""
    runner = RecordingRunner()
    runner.answer(STOP_ARGV, {"ok": True, "action": "stop", "result": "stopped"})
    procs = [ProcessInfo(402, "node.exe", r"node C:\openclaw\dist\index.js gateway")]

    service_for(fake_env, runner, process_lister=lambda: procs).stop()

    assert not any(call[0] in {"taskkill.exe", "kill", "wscript.exe"} for call in runner.calls)


def test_restart_runs_the_service_restart_and_waits_for_the_port(fake_env) -> None:
    runner = RecordingRunner()
    runner.answer(RESTART_ARGV, {"ok": True, "action": "restart", "result": "restarted", "message": "restarted"})
    clock = FakeClock()
    probes = [False, False, True]

    def probe_port(port: int, host: str = "127.0.0.1") -> bool:
        if probes:
            return probes.pop(0)
        return True

    res = service_for(fake_env, runner, platform="linux", process_lister=lambda: [],
                      port_probe=probe_port, time_provider=clock.time, sleeper=clock.sleep).restart(
        wait=True, timeout_s=600.0)

    assert res["ok"] is True
    assert res["commands"] == [RESTART_ARGV]
    assert res["port_listening"] is True
    assert res["elapsed_s"] >= 2.0
    assert len(clock.sleeps) >= 2


def test_restart_times_out(fake_env) -> None:
    runner = RecordingRunner()
    runner.answer(RESTART_ARGV, {"ok": True, "action": "restart", "result": "restarted"})
    clock = FakeClock()

    res = service_for(fake_env, runner, platform="linux", process_lister=lambda: [],
                      port_probe=lambda port, host="127.0.0.1": False,
                      time_provider=clock.time, sleeper=clock.sleep).restart(wait=True, timeout_s=10.0)

    assert res["ok"] is False
    assert "Timed out" in res["error"]
    assert res["elapsed_s"] >= 10.0


def test_a_refused_restart_is_reported_and_nothing_is_waited_for(fake_env) -> None:
    """A refusal never activated anything: the old engine then started a second gateway."""
    runner = RecordingRunner()
    runner.answer(RESTART_ARGV, {"ok": False, "action": "restart",
                                 "error": "Gateway restart failed: multiple gateway processes are listening"},
                  code=1)
    clock = FakeClock()

    res = service_for(fake_env, runner, process_lister=lambda: [],
                      port_probe=lambda port, host="127.0.0.1": False,
                      time_provider=clock.time, sleeper=clock.sleep).restart()

    assert res["ok"] is False
    assert "multiple gateway processes" in res["error"]
    assert clock.sleeps == []
    assert runner.calls == [RESTART_ARGV]


def test_a_restart_that_missed_its_own_health_check_is_still_waited_for(fake_env) -> None:
    """OpenClaw's own health budget can be shorter than this engine's cold start."""
    runner = RecordingRunner()
    runner.answer(RESTART_ARGV, {"ok": False, "action": "restart", "result": "restart-health-failed",
                                 "error": "Gateway restart timed out after 45s waiting for health checks."},
                  code=1)
    clock = FakeClock()

    res = service_for(fake_env, runner, process_lister=lambda: [],
                      port_probe=lambda port, host="127.0.0.1": clock.time() >= 2.0,
                      time_provider=clock.time, sleeper=clock.sleep).restart(wait=True, timeout_s=600.0)

    assert res["ok"] is True
    assert res["elapsed_s"] >= 2.0


def test_stopped_context_manager_restarts_even_after_exception(fake_env) -> None:
    runner = RecordingRunner()
    runner.answer(STOP_ARGV, {"ok": True, "action": "stop", "result": "stopped"})
    runner.answer(START_ARGV, {"ok": True, "action": "start", "result": "started"})

    service = service_for(fake_env, runner, process_lister=lambda: [],
                          port_probe=lambda port, host="127.0.0.1": True)

    with pytest.raises(ValueError, match="simulated failure"):
        with service.stopped(wait=False):
            assert runner.calls == [STOP_ARGV]
            raise ValueError("simulated failure")

    assert runner.calls == [STOP_ARGV, START_ARGV]


def test_uninstall_asks_openclaw_and_touches_no_file_of_its_own(fake_env) -> None:
    """The engine removed the Startup VBS and the unit itself; OpenClaw owns both now."""
    vbs_file = fake_env["startup"] / "OpenClaw Gateway.vbs"
    vbs_file.write_text("vbs", encoding="utf-8")
    cfg_file = fake_env["state_dir"] / "openclaw.json"
    cfg_file.write_text('{"gateway": {"port": 18789}}', encoding="utf-8")
    runner = RecordingRunner()
    runner.answer(UNINSTALL_ARGV, {"ok": True, "action": "uninstall", "result": "uninstalled"})

    res = service_for(fake_env, runner, process_lister=lambda: []).uninstall()

    assert res["ok"] is True
    assert res["commands"] == [UNINSTALL_ARGV]
    assert vbs_file.exists(), "a launcher the engine did not create is never deleted by the engine"
    assert cfg_file.exists(), "openclaw.json must never be touched by uninstall"
    assert fake_env["state_dir"].exists(), "state_dir must never be touched by uninstall"
    assert runner.calls == [UNINSTALL_ARGV]


def test_dry_run_executes_no_processes(fake_env) -> None:
    runner = RecordingRunner()
    procs = [ProcessInfo(601, "node.exe", r"node C:\openclaw\dist\index.js gateway")]
    service = service_for(fake_env, runner, process_lister=lambda: procs,
                          port_probe=lambda port, host="127.0.0.1": True)

    assert service.install(dry_run=True)["dry_run"] is True
    assert service.stop(dry_run=True)["dry_run"] is True
    assert service.start(dry_run=True)["dry_run"] is True
    assert service.restart(dry_run=True)["dry_run"] is True
    assert service.uninstall(dry_run=True)["dry_run"] is True
    assert service.status(dry_run=True)["dry_run"] is True

    assert runner.calls == [], "a dry run runs nothing at all"


def test_module_level_shortcuts(fake_env) -> None:
    runner = RecordingRunner(default_code=0)
    st = service_status(platform="linux", home=fake_env["home"], runner=runner,
                        process_lister=lambda: [], port_probe=lambda p, h="127.0.0.1": False, dry_run=True)
    assert st["dry_run"] is True

    inst = service_install(platform="linux", home=fake_env["home"], runner=runner, dry_run=True)
    assert inst["dry_run"] is True

    stp = service_stop(platform="linux", home=fake_env["home"], runner=runner, dry_run=True)
    assert stp["dry_run"] is True

    strt = service_start(platform="linux", home=fake_env["home"], runner=runner, dry_run=True)
    assert strt["dry_run"] is True

    rst = service_restart(wait=False, platform="linux", home=fake_env["home"], runner=runner, dry_run=True)
    assert rst["dry_run"] is True

    with service_stopped(wait=False, platform="linux", home=fake_env["home"], runner=runner, dry_run=True):
        pass

    uninst = service_uninstall(platform="linux", home=fake_env["home"], runner=runner, dry_run=True)
    assert uninst["dry_run"] is True

    assert runner.calls == [], "not one of those shortcuts ran a command"


# ------------------------------------------------ the registration somebody else made


def test_the_second_registration_is_not_the_node_host() -> None:
    """A node connects out and opens no port here: it is not a second gateway."""
    runner = RecordingRunner()
    runner.answer(DEEP_STATUS_ARGV, {
        "extraServices": [
            {"platform": "win32", "label": "\\OpenClaw Node",
             "detail": "task: \\OpenClaw Node, run: C:\\Users\\me\\.openclaw\\node.vbs",
             "scope": "system", "marker": "openclaw", "legacy": False},
            {"platform": "win32", "label": "OpenClaw Gateway",
             "detail": "startup: C:\\Users\\me\\Startup\\OpenClaw Gateway.vbs",
             "scope": "user", "marker": "openclaw", "legacy": False},
        ]
    })

    found = OpenClawService(platform="win32", runner=runner).extra_services()

    assert [item["label"] for item in found] == ["OpenClaw Gateway"]
    assert runner.calls == [DEEP_STATUS_ARGV]


def test_a_deep_inventory_that_did_not_answer_reports_no_registration() -> None:
    runner = RecordingRunner(default_code=1)

    assert OpenClawService(platform="win32", runner=runner).extra_services() == []


def test_the_removal_step_is_the_command_that_fits_the_shape_openclaw_reported() -> None:
    """Printed, never run: the engine did not create that registration."""
    startup = {"platform": "win32", "label": "OpenClaw Gateway",
               "detail": r"startup: C:\Users\user\Startup\OpenClaw Gateway.vbs", "scope": "user"}
    task = {"platform": "win32", "label": r"\OpenClaw Gateway",
            "detail": r"task: \OpenClaw Gateway, run: C:\Users\user\.openclaw\gateway.cmd", "scope": "system"}
    unit = {"platform": "linux", "label": "openclaw-gateway-extra.service",
            "detail": "unit: /home/user/.config/systemd/user/openclaw-gateway-extra.service", "scope": "user"}
    backup = {"platform": "linux", "label": "clawdbot-gateway.service",
              "detail": "unit backup: /home/user/.config/systemd/user/clawdbot-gateway.service.bak", "scope": "user"}
    plist = {"platform": "darwin", "label": "ai.openclaw.gateway.second",
             "detail": "plist: /Users/me/Library/LaunchAgents/ai.openclaw.gateway.second.plist", "scope": "user"}

    assert removal_commands(startup) == [r"Remove-Item -LiteralPath 'C:\Users\user\Startup\OpenClaw Gateway.vbs'"]
    assert removal_commands(task) == [r'schtasks.exe /Delete /TN "\OpenClaw Gateway" /F']
    assert removal_commands(unit) == [
        "systemctl --user disable --now openclaw-gateway-extra.service",
        "rm '/home/user/.config/systemd/user/openclaw-gateway-extra.service'",
        "systemctl --user daemon-reload",
    ]
    assert removal_commands(backup) == ["rm '/home/user/.config/systemd/user/clawdbot-gateway.service.bak'"]
    assert removal_commands(plist) == [
        "launchctl bootout gui/$UID/ai.openclaw.gateway.second",
        "rm '/Users/me/Library/LaunchAgents/ai.openclaw.gateway.second.plist'",
    ]
    assert removal_commands({"platform": "win32", "label": "x", "detail": "???"}) == []


# ---------------------------------------------------------------------------- pairing


def test_pairing_qr_returns_terminal_command() -> None:
    cmd_info = pairing_qr()
    assert "command" in cmd_info
    assert cmd_info["command"] == "openclaw qr"


def test_pairing_pending_parses_json_and_strips_tokens() -> None:
    payload = json.dumps([
        {"id": "req-1", "device": "iPhone", "token": "secret-tok-123", "status": "pending"},
        {"id": "req-2", "device": "Android", "authKey": "secret-key-456", "status": "pending"},
    ])
    runner = RecordingRunner(default_code=0, default_stdout=payload)

    res = pairing_pending(runner=runner)
    assert res["ok"] is True
    assert len(res["devices"]) == 2
    for dev in res["devices"]:
        assert "token" not in dev
        assert "authKey" not in dev


def test_pairing_pending_text_fallback() -> None:
    stdout = "Pending pairing requests:\n  - ID: dev-999 (Pixel 8)\n"
    runner = RecordingRunner(default_code=0, default_stdout=stdout)

    res = pairing_pending(runner=runner)
    assert res["ok"] is True
    assert len(res["devices"]) >= 1
    assert res["devices"][0]["id"] == "dev-999"


def test_the_text_fallback_never_copies_a_token_into_the_summary() -> None:
    """The summary used to be the raw line, and a token on it reached the screen."""
    stdout = (
        "Pending pairing requests:\n"
        "  - ID: dev-999 (Pixel 8) token: oc_pair_secret_value\n"
        "  - ID: dev-1000 (iPad) authKey=oc_auth_secret_value\n"
    )
    runner = RecordingRunner(default_code=0, default_stdout=stdout)

    res = pairing_pending(runner=runner)

    rendered = json.dumps(res)
    assert res["devices"][0]["id"] == "dev-999"
    assert "oc_pair_secret_value" not in rendered
    assert "oc_auth_secret_value" not in rendered
    assert "[redacted]" in res["devices"][0]["summary"]


def test_the_approval_output_never_returns_a_token() -> None:
    stdout = "Device dev-999 approved\npairing token: oc_pair_secret_value\n"
    runner = RecordingRunner(default_code=0, default_stdout=stdout)

    res = pairing_approve("dev-999", runner=runner)

    assert res["ok"] is True
    assert "oc_pair_secret_value" not in json.dumps(res)
    assert "[redacted]" in res["output"]


def test_pairing_approve_calls_runner() -> None:
    runner = RecordingRunner(default_code=0, default_stdout="Device dev-999 approved")
    res = pairing_approve("dev-999", runner=runner)
    assert res["ok"] is True
    assert ["openclaw", "devices", "approve", "dev-999"] in runner.calls


def test_pairing_dry_run_executes_nothing() -> None:
    runner = RecordingRunner(default_code=0)
    res_p = pairing_pending(runner=runner, dry_run=True)
    assert res_p["dry_run"] is True
    res_a = pairing_approve("dev-1", runner=runner, dry_run=True)
    assert res_a["dry_run"] is True
    assert len(runner.calls) == 0


def test_a_batch_named_gateway_cmd_that_is_not_openclaw_is_not_our_gateway() -> None:
    """Any `cmd.exe /c ...\\gateway.cmd` used to pass, and `stop()` force-killed its tree."""
    assert is_gateway_process(ProcessInfo(901, "cmd.exe", r"cmd.exe /c C:\build\gateway.cmd")) is False
    assert is_gateway_process(
        ProcessInfo(902, "cmd.exe", r"cmd.exe /c C:\openclaw\gateway.cmd")
    ) is True


# The shape `openclaw devices list --json` prints in 2026.10.6
# (dist/devices-cli.runtime-CllfBpVQ.mjs:234-239): two tables, and the id of a
# waiting request is `requestId`, which is what `devices approve` takes.
def _devices_payload() -> str:
    return json.dumps({
        "pending": [{
            "requestId": "req-7c1",
            "deviceId": "9f2b",
            "displayName": "OpenClaw Companion",
            "roles": ["operator"],
            "scopes": ["operator.read"],
            "ts": 1761000000000,
        }],
        "paired": [{
            "deviceId": "aa11",
            "displayName": "Pixel 8",
            "clientId": "cli",
            "platform": "android",
        }],
    })


def test_pairing_pending_reads_the_requests_table_of_the_real_payload() -> None:
    """`pending` is the table with the requests: `paired` is not one of them."""
    runner = RecordingRunner(default_code=0, default_stdout=_devices_payload())

    devices = pairing_pending(runner=runner)["devices"]

    assert len(devices) == 1, "a device already paired is not a request to approve"
    assert devices[0]["id"] == "req-7c1", "the id `devices approve` takes is requestId"


def test_the_listed_devices_are_requests_and_devices_together() -> None:
    """What the gateway knows about this machine: the app is in `paired` once it said yes."""
    runner = RecordingRunner(default_code=0, default_stdout=_devices_payload())

    devices = pairing_listed(runner=runner)

    assert devices is not None
    assert [device["id"] for device in devices] == ["req-7c1", "aa11"]


def test_pairing_pending_failure_reports_error_without_tokens() -> None:
    runner = RecordingRunner(
        default_code=1,
        default_stdout="",
        default_stderr="requires credentials before opening a websocket token: secret123",
    )
    res = pairing_pending(runner=runner)
    assert res["ok"] is False
    assert res["devices"] == []
    assert "requires credentials before opening a websocket" in res["error"]
    assert "secret123" not in res["error"]
    assert "[redacted]" in res["error"]
