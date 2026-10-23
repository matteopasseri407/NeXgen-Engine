"""Tests for OpenClaw gateway service and pairing management.

Black-box verification with injected runners, process listers, and port probes.
No real system processes, ports, or files under ~ are touched.
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
    pending as pairing_pending,
    qr as pairing_qr,
)


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

    def __call__(self, argv: list[str]) -> RunResult:
        self.calls.append(list(argv))
        key = tuple(argv)
        if key in self.canned:
            return self.canned[key]
        return RunResult(self.default_code, self.default_stdout, self.default_stderr)


def killable(procs: list[ProcessInfo], runner: RecordingRunner):
    """A process list that loses a process once `taskkill` has been sent for it."""
    def lister() -> list[ProcessInfo]:
        killed = {call[2] for call in runner.calls if call[:2] == ["taskkill.exe", "/PID"]}
        return [p for p in procs if str(p.pid) not in killed]
    return lister


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
    systemd_user = home / ".config" / "systemd" / "user"
    systemd_user.mkdir(parents=True)

    return {
        "home": home,
        "state_dir": state_dir,
        "appdata": appdata,
        "startup": startup,
        "localappdata": localappdata,
        "packages": packages,
        "systemd_user": systemd_user,
    }


def test_status_windows_vbs_installed(fake_env) -> None:
    vbs_file = fake_env["startup"] / "OpenClaw Gateway.vbs"
    vbs_file.write_text("' OpenClaw Gateway launcher", encoding="utf-8")

    runner = RecordingRunner()
    processes = [
        ProcessInfo(101, "node.exe", r"node.exe C:\tools\openclaw\dist\index.js gateway --port 18789"),
    ]

    service = OpenClawService(
        platform="win32",
        home=fake_env["home"],
        state_dir=fake_env["state_dir"],
        appdata=fake_env["appdata"],
        localappdata=fake_env["localappdata"],
        runner=runner,
        process_lister=lambda: processes,
        port_probe=lambda port, host="127.0.0.1": True,
    )

    st = service.status()
    assert st["installed"] is True
    assert st["mechanism"] == "vbs"
    assert st["process_count"] == 1
    assert st["port_listening"] is True
    assert st["companion_present"] is False
    assert st["dual_gateway"] is False


def test_status_windows_schtasks_installed(fake_env) -> None:
    runner = RecordingRunner()
    runner.set_canned(["schtasks.exe", "/Query", "/TN", "OpenClaw Gateway"], code=0, stdout="SUCCESS")

    service = OpenClawService(
        platform="win32",
        home=fake_env["home"],
        state_dir=fake_env["state_dir"],
        appdata=fake_env["appdata"],
        localappdata=fake_env["localappdata"],
        runner=runner,
        process_lister=lambda: [],
        port_probe=lambda port, host="127.0.0.1": False,
    )

    st = service.status()
    assert st["installed"] is True
    assert st["mechanism"] == "schtasks"
    assert st["process_count"] == 0
    assert st["port_listening"] is False


def test_status_linux_systemd_installed(fake_env) -> None:
    unit_file = fake_env["systemd_user"] / "openclaw-gateway.service"
    unit_file.write_text("[Unit]\nDescription=OpenClaw Gateway\n", encoding="utf-8")

    runner = RecordingRunner()
    processes = [
        ProcessInfo(202, "node", "node /usr/lib/node_modules/openclaw/dist/index.js gateway"),
    ]

    service = OpenClawService(
        platform="linux",
        home=fake_env["home"],
        state_dir=fake_env["state_dir"],
        systemd_user_dir=fake_env["systemd_user"],
        runner=runner,
        process_lister=lambda: processes,
        port_probe=lambda port, host="127.0.0.1": True,
    )

    st = service.status()
    assert st["installed"] is True
    assert st["mechanism"] == "systemd"
    assert st["process_count"] == 1
    assert st["port_listening"] is True


def test_status_not_installed(fake_env) -> None:
    runner = RecordingRunner(default_code=1)
    service = OpenClawService(
        platform="win32",
        home=fake_env["home"],
        state_dir=fake_env["state_dir"],
        appdata=fake_env["appdata"],
        localappdata=fake_env["localappdata"],
        runner=runner,
        process_lister=lambda: [],
        port_probe=lambda port, host="127.0.0.1": False,
    )

    st = service.status()
    assert st["installed"] is False
    assert st["mechanism"] is None
    assert st["process_count"] == 0


def test_status_dual_gateway_processes(fake_env) -> None:
    runner = RecordingRunner(default_code=1)
    processes = [
        ProcessInfo(301, "node.exe", r"node C:\openclaw\dist\index.js gateway"),
        ProcessInfo(302, "node.exe", r"node C:\openclaw\dist\index.js gateway"),
    ]

    service = OpenClawService(
        platform="win32",
        home=fake_env["home"],
        state_dir=fake_env["state_dir"],
        appdata=fake_env["appdata"],
        localappdata=fake_env["localappdata"],
        runner=runner,
        process_lister=lambda: processes,
        port_probe=lambda port, host="127.0.0.1": True,
    )

    st = service.status()
    assert st["process_count"] == 2
    assert st["dual_gateway"] is True


def test_status_detects_microsoft_store_companion(fake_env) -> None:
    comp_pkg = fake_env["packages"] / "OpenClaw.CompanionApp_8wekyb3d8bbwe"
    comp_pkg.mkdir()
    comp_lnk = fake_env["startup"] / "OpenClaw Companion.lnk"
    comp_lnk.write_text("fake shortcut", encoding="utf-8")

    runner = RecordingRunner(default_code=1)
    service = OpenClawService(
        platform="win32",
        home=fake_env["home"],
        state_dir=fake_env["state_dir"],
        appdata=fake_env["appdata"],
        localappdata=fake_env["localappdata"],
        runner=runner,
        process_lister=lambda: [],
        port_probe=lambda port, host="127.0.0.1": False,
    )

    st = service.status()
    assert st["companion_present"] is True
    assert "Remove-AppxPackage" in (st["companion_removal_command"] or "")


def test_install_invokes_openclaw_gateway_install(fake_env) -> None:
    runner = RecordingRunner(default_code=0)
    service = OpenClawService(
        platform="linux",
        home=fake_env["home"],
        state_dir=fake_env["state_dir"],
        systemd_user_dir=fake_env["systemd_user"],
        runner=runner,
        process_lister=lambda: [],
        port_probe=lambda port, host="127.0.0.1": False,
    )

    res = service.install()
    assert res["ok"] is True
    assert ["openclaw", "gateway", "install"] in runner.calls


def test_stop_windows_kills_node_and_cmd_processes(fake_env) -> None:
    runner = RecordingRunner(default_code=0)
    procs = [
        ProcessInfo(401, "node.exe", r"node C:\openclaw\dist\index.js gateway --port 18789"),
        ProcessInfo(402, "cmd.exe", r"cmd.exe /c C:\openclaw\gateway.cmd"),
        ProcessInfo(403, "other.exe", "some other process"),
    ]

    service = OpenClawService(
        platform="win32",
        home=fake_env["home"],
        state_dir=fake_env["state_dir"],
        appdata=fake_env["appdata"],
        localappdata=fake_env["localappdata"],
        runner=runner,
        process_lister=killable(procs, runner),
        port_probe=lambda port, host="127.0.0.1": False,
    )

    res = service.stop()
    assert res["ok"] is True
    assert res["remaining_pids"] == []
    assert 401 in res["stopped_pids"]
    assert 402 in res["stopped_pids"]
    assert 403 not in res["stopped_pids"]
    assert ["taskkill.exe", "/PID", "401", "/T", "/F"] in runner.calls
    assert ["taskkill.exe", "/PID", "402", "/T", "/F"] in runner.calls


def test_a_batch_named_gateway_cmd_that_is_not_openclaw_is_not_our_gateway() -> None:
    """Any `cmd.exe /c ...\\gateway.cmd` used to pass, and `stop()` force-killed its tree."""
    assert is_gateway_process(ProcessInfo(901, "cmd.exe", r"cmd.exe /c C:\build\gateway.cmd")) is False
    assert is_gateway_process(
        ProcessInfo(902, "cmd.exe", r"cmd.exe /c C:\openclaw\gateway.cmd")
    ) is True


def test_stop_linux_uses_systemctl(fake_env) -> None:
    runner = RecordingRunner(default_code=0)
    service = OpenClawService(
        platform="linux",
        home=fake_env["home"],
        state_dir=fake_env["state_dir"],
        systemd_user_dir=fake_env["systemd_user"],
        runner=runner,
        process_lister=lambda: [],
        port_probe=lambda port, host="127.0.0.1": False,
    )

    res = service.stop()
    assert res["ok"] is True
    assert ["systemctl", "--user", "stop", "openclaw-gateway.service"] in runner.calls


def test_start_windows_with_vbs(fake_env) -> None:
    vbs_file = fake_env["startup"] / "OpenClaw Gateway.vbs"
    vbs_file.write_text("wscript code", encoding="utf-8")

    runner = RecordingRunner(default_code=1)  # schtasks fails, falls back to vbs
    service = OpenClawService(
        platform="win32",
        home=fake_env["home"],
        state_dir=fake_env["state_dir"],
        appdata=fake_env["appdata"],
        localappdata=fake_env["localappdata"],
        runner=runner,
        process_lister=lambda: [],
        port_probe=lambda port, host="127.0.0.1": False,
    )

    res = service.start()
    assert res["ok"] is True
    assert ["wscript.exe", str(vbs_file)] in runner.calls


def test_start_windows_without_a_vbs_file_uses_the_openclaw_command(fake_env) -> None:
    """The Startup folder always exists, so a VBS path was always truthy.

    On a machine where neither the scheduled task nor the launcher file is
    there, `start()` used to run `wscript.exe` on a file that does not exist:
    Windows showed an error and the gateway never started.
    """
    runner = RecordingRunner(default_code=1)  # schtasks does not know the task either
    service = OpenClawService(
        platform="win32",
        home=fake_env["home"],
        state_dir=fake_env["state_dir"],
        appdata=fake_env["appdata"],
        localappdata=fake_env["localappdata"],
        runner=runner,
        process_lister=lambda: [],
        port_probe=lambda port, host="127.0.0.1": False,
    )

    res = service.start()

    assert res["ok"] is True
    assert ["openclaw", "gateway", "start"] in runner.calls
    assert not any(call and call[0] == "wscript.exe" for call in runner.calls)


def test_start_windows_with_schtasks(fake_env) -> None:
    runner = RecordingRunner()
    runner.set_canned(["schtasks.exe", "/Query", "/TN", "OpenClaw Gateway"], code=0)

    service = OpenClawService(
        platform="win32",
        home=fake_env["home"],
        state_dir=fake_env["state_dir"],
        appdata=fake_env["appdata"],
        localappdata=fake_env["localappdata"],
        runner=runner,
        process_lister=lambda: [],
        port_probe=lambda port, host="127.0.0.1": False,
    )

    res = service.start()
    assert res["ok"] is True
    assert ["schtasks.exe", "/Run", "/TN", "OpenClaw Gateway"] in runner.calls


def test_start_linux_with_systemctl(fake_env) -> None:
    runner = RecordingRunner(default_code=0)
    service = OpenClawService(
        platform="linux",
        home=fake_env["home"],
        state_dir=fake_env["state_dir"],
        systemd_user_dir=fake_env["systemd_user"],
        runner=runner,
        process_lister=lambda: [],
        port_probe=lambda port, host="127.0.0.1": False,
    )

    res = service.start()
    assert res["ok"] is True
    assert ["systemctl", "--user", "start", "openclaw-gateway.service"] in runner.calls


def test_restart_waits_for_port_and_succeeds(fake_env) -> None:
    runner = RecordingRunner(default_code=0)
    clock = FakeClock()
    probes = [False, False, True]

    def probe_port(port: int, host: str = "127.0.0.1") -> bool:
        if probes:
            return probes.pop(0)
        return True

    service = OpenClawService(
        platform="linux",
        home=fake_env["home"],
        state_dir=fake_env["state_dir"],
        systemd_user_dir=fake_env["systemd_user"],
        runner=runner,
        process_lister=lambda: [],
        port_probe=probe_port,
        time_provider=clock.time,
        sleeper=clock.sleep,
    )

    res = service.restart(wait=True, timeout_s=600.0)
    assert res["ok"] is True
    assert res["port_listening"] is True
    assert res["elapsed_s"] >= 2.0
    assert len(clock.sleeps) >= 2


def test_restart_times_out(fake_env) -> None:
    runner = RecordingRunner(default_code=0)
    clock = FakeClock()

    service = OpenClawService(
        platform="linux",
        home=fake_env["home"],
        state_dir=fake_env["state_dir"],
        systemd_user_dir=fake_env["systemd_user"],
        runner=runner,
        process_lister=lambda: [],
        port_probe=lambda port, host="127.0.0.1": False,
        time_provider=clock.time,
        sleeper=clock.sleep,
    )

    res = service.restart(wait=True, timeout_s=10.0)
    assert res["ok"] is False
    assert "Timed out" in res["error"]
    assert res["elapsed_s"] >= 10.0


def test_stopped_context_manager_restarts_even_after_exception(fake_env) -> None:
    vbs_file = fake_env["startup"] / "OpenClaw Gateway.vbs"
    vbs_file.write_text("vbs", encoding="utf-8")

    runner = RecordingRunner(default_code=1)
    procs = [ProcessInfo(501, "node.exe", r"node C:\openclaw\dist\index.js gateway")]
    started = []

    def probe(port, host="127.0.0.1"):
        # The port is held until the kill, free while stopped, open again once restarted.
        if any(call[:1] == ["wscript.exe"] for call in runner.calls):
            started.append(True)
            return True
        return not any(call[:1] == ["taskkill.exe"] for call in runner.calls)

    service = OpenClawService(
        platform="win32",
        home=fake_env["home"],
        state_dir=fake_env["state_dir"],
        appdata=fake_env["appdata"],
        localappdata=fake_env["localappdata"],
        runner=runner,
        process_lister=killable(procs, runner),
        port_probe=probe,
    )

    with pytest.raises(ValueError, match="simulated failure"):
        with service.stopped(wait=False):
            assert ["taskkill.exe", "/PID", "501", "/T", "/F"] in runner.calls
            raise ValueError("simulated failure")

    assert ["wscript.exe", str(vbs_file)] in runner.calls


def test_dry_run_executes_no_processes(fake_env) -> None:
    runner = RecordingRunner(default_code=0)
    procs = [ProcessInfo(601, "node.exe", r"node C:\openclaw\dist\index.js gateway")]

    service = OpenClawService(
        platform="win32",
        home=fake_env["home"],
        state_dir=fake_env["state_dir"],
        appdata=fake_env["appdata"],
        localappdata=fake_env["localappdata"],
        runner=runner,
        process_lister=lambda: procs,
        port_probe=lambda port, host="127.0.0.1": True,
    )

    res_inst = service.install(dry_run=True)
    assert res_inst["dry_run"] is True
    res_stop = service.stop(dry_run=True)
    assert res_stop["dry_run"] is True
    res_start = service.start(dry_run=True)
    assert res_start["dry_run"] is True
    res_restart = service.restart(dry_run=True)
    assert res_restart["dry_run"] is True
    res_uninst = service.uninstall(dry_run=True)
    assert res_uninst["dry_run"] is True

    # Absolutely zero runner calls executed
    assert len(runner.calls) == 0


def test_uninstall_windows_cleans_service_without_touching_config(fake_env) -> None:
    vbs_file = fake_env["startup"] / "OpenClaw Gateway.vbs"
    vbs_file.write_text("vbs", encoding="utf-8")
    cfg_file = fake_env["state_dir"] / "openclaw.json"
    cfg_file.write_text('{"gateway": {"port": 18789}}', encoding="utf-8")

    runner = RecordingRunner(default_code=0)
    service = OpenClawService(
        platform="win32",
        home=fake_env["home"],
        state_dir=fake_env["state_dir"],
        appdata=fake_env["appdata"],
        localappdata=fake_env["localappdata"],
        runner=runner,
        process_lister=lambda: [],
        port_probe=lambda port, host="127.0.0.1": False,
    )

    res = service.uninstall()
    assert res["ok"] is True
    assert not vbs_file.exists()
    assert cfg_file.exists(), "openclaw.json must never be touched by uninstall"
    assert fake_env["state_dir"].exists(), "state_dir must never be touched by uninstall"


def test_uninstall_linux_removes_systemd_unit(fake_env) -> None:
    unit_file = fake_env["systemd_user"] / "openclaw-gateway.service"
    unit_file.write_text("[Unit]\nDescription=Gateway\n", encoding="utf-8")

    runner = RecordingRunner(default_code=0)
    service = OpenClawService(
        platform="linux",
        home=fake_env["home"],
        state_dir=fake_env["state_dir"],
        systemd_user_dir=fake_env["systemd_user"],
        runner=runner,
        process_lister=lambda: [],
        port_probe=lambda port, host="127.0.0.1": False,
    )

    res = service.uninstall()
    assert res["ok"] is True
    assert not unit_file.exists()
    assert ["systemctl", "--user", "daemon-reload"] in runner.calls


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


def test_module_level_shortcuts(fake_env) -> None:
    runner = RecordingRunner(default_code=0)
    st = service_status(platform="linux", home=fake_env["home"], runner=runner, process_lister=lambda: [], port_probe=lambda p, h="127.0.0.1": False, dry_run=True)
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


def test_stop_waits_until_the_old_gateway_lets_go_of_the_port(fake_env) -> None:
    """taskkill returns before Windows frees the port: starting at once raced the old gateway."""
    runner = RecordingRunner(default_code=0)
    clock = FakeClock()
    procs = [ProcessInfo(701, "node.exe", r"node C:\openclaw\dist\index.js gateway --port 18789")]
    port_free_at = 3.0

    service = OpenClawService(
        platform="win32",
        home=fake_env["home"],
        state_dir=fake_env["state_dir"],
        appdata=fake_env["appdata"],
        localappdata=fake_env["localappdata"],
        runner=runner,
        process_lister=killable(procs, runner),
        port_probe=lambda port, host="127.0.0.1": clock.time() < port_free_at,
        time_provider=clock.time,
        sleeper=clock.sleep,
    )

    res = service.stop()
    assert res["ok"] is True
    assert clock.time() >= port_free_at


def test_a_gateway_that_survives_the_kill_stops_the_restart(fake_env) -> None:
    runner = RecordingRunner(default_code=0)
    clock = FakeClock()
    vbs_file = fake_env["startup"] / "OpenClaw Gateway.vbs"
    vbs_file.write_text("vbs", encoding="utf-8")
    stubborn = [ProcessInfo(801, "node.exe", r"node C:\openclaw\dist\index.js gateway")]

    service = OpenClawService(
        platform="win32",
        home=fake_env["home"],
        state_dir=fake_env["state_dir"],
        appdata=fake_env["appdata"],
        localappdata=fake_env["localappdata"],
        runner=runner,
        process_lister=lambda: stubborn,
        port_probe=lambda port, host="127.0.0.1": True,
        time_provider=clock.time,
        sleeper=clock.sleep,
    )

    res = service.restart()
    assert res["ok"] is False
    assert "801" in res["error"]
    assert ["wscript.exe", str(vbs_file)] not in runner.calls, "never start a second gateway beside a live one"
