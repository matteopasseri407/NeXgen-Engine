"""Test: on Windows the shared Chrome starts on the user's desktop, whoever launches it.

A child process inherits its parent's desktop. Antigravity runs its shell on
a private sandbox desktop, so a plain Popen from there gave a Chrome whose
CDP port answered while no window ever reached the user (issue #136).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.tools import chrome  # noqa: E402

SANDBOX_DESKTOP = "WinSta0\\exebox-TBP2VOTBHK2WVWJC2XIZANDBBQ"


@pytest.fixture
def windows_launch(monkeypatch, tmp_path: Path):
    """A Windows launch with every host call recorded instead of made."""
    calls: dict[str, list] = {"run": [], "popen": [], "xml": []}
    state = {"desktop": SANDBOX_DESKTOP, "cdp": [False, True], "run_rc": [0, 0]}

    def fake_run(cmd, **kwargs):
        calls["run"].append(cmd)
        if "/XML" in cmd:
            calls["xml"].append(Path(cmd[cmd.index("/XML") + 1]).read_text(encoding="utf-16"))
        return subprocess.CompletedProcess(cmd, state["run_rc"].pop(0), "", "access denied")

    def fake_cdp():
        return state["cdp"].pop(0) if len(state["cdp"]) > 1 else state["cdp"][0]

    monkeypatch.setattr(chrome.sys, "platform", "win32")
    monkeypatch.setattr(chrome, "find_chrome_executable", lambda: "C:\\Chrome\\chrome.exe")
    monkeypatch.setattr(chrome, "get_profile_dir", lambda: tmp_path / "Chrome Debug")
    monkeypatch.setattr(chrome, "windows_desktop", lambda: state["desktop"])
    monkeypatch.setattr(chrome, "is_cdp_up", fake_cdp)
    monkeypatch.setattr(chrome.subprocess, "run", fake_run)
    monkeypatch.setattr(chrome.subprocess, "Popen", lambda cmd, **kw: calls["popen"].append(cmd))
    monkeypatch.setattr(chrome.time, "sleep", lambda s: None)
    return calls, state


def test_sandbox_desktop_starts_chrome_through_the_task(windows_launch):
    calls, _ = windows_launch

    assert chrome.launch_chrome([]) == 0

    assert [c[:2] for c in calls["run"]] == [["schtasks.exe", "/Create"], ["schtasks.exe", "/Run"]]
    assert calls["run"][1] == ["schtasks.exe", "/Run", "/TN", "NeXgen Agent Chrome"]
    assert calls["popen"] == []


def test_sandbox_desktop_hands_the_url_to_the_running_browser(windows_launch):
    calls, _ = windows_launch

    assert chrome.launch_chrome(["https://example.com"]) == 0

    assert len(calls["popen"]) == 1
    assert calls["popen"][0][0] == "C:\\Chrome\\chrome.exe"
    assert calls["popen"][0][-1] == "https://example.com"
    assert "https://example.com" not in calls["xml"][0]


def test_user_desktop_keeps_the_plain_launch(windows_launch):
    calls, state = windows_launch
    state["desktop"] = "WinSta0\\Default"

    assert chrome.launch_chrome([]) == 0

    assert calls["run"] == []
    assert len(calls["popen"]) == 1


def test_running_browser_keeps_the_plain_handoff(windows_launch):
    calls, state = windows_launch
    state["cdp"] = [True]

    assert chrome.launch_chrome(["https://example.com"]) == 0

    assert calls["run"] == []
    assert calls["popen"][0][-1] == "https://example.com"


def test_unknown_desktop_keeps_the_plain_launch(windows_launch):
    calls, state = windows_launch
    state["desktop"] = None

    assert chrome.launch_chrome([]) == 0

    assert calls["run"] == []
    assert len(calls["popen"]) == 1


def test_task_failure_never_falls_back_to_an_invisible_chrome(windows_launch, capsys):
    calls, state = windows_launch
    state["run_rc"] = [1]

    assert chrome.launch_chrome([]) == 1

    assert calls["popen"] == []
    assert len(calls["run"]) == 1
    assert "access denied" in capsys.readouterr().err


def test_port_that_never_answers_is_reported(windows_launch, monkeypatch):
    calls, state = windows_launch
    state["cdp"] = [False]
    monkeypatch.setattr(chrome, "TASK_START_SECONDS", 0.0)

    assert chrome.launch_chrome(["https://example.com"]) == 1

    assert calls["popen"] == []


def test_task_definition():
    xml = chrome.task_xml(["C:\\Program Files\\Chrome\\chrome.exe", "--user-data-dir=C:\\A & B\\profile", "--no-first-run"])

    assert "<Command>C:\\Program Files\\Chrome\\chrome.exe</Command>" in xml
    assert '<Arguments>"--user-data-dir=C:\\A &amp; B\\profile" --no-first-run</Arguments>' in xml
    assert "<LogonType>InteractiveToken</LogonType>" in xml
    assert "<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>" in xml
    assert "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>" in xml
    assert "<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>" in xml
    assert "<Priority>4</Priority>" in xml
    assert "<Triggers>" not in xml


@pytest.mark.skipif(sys.platform != "win32", reason="reads this process's own Windows desktop")
def test_windows_desktop_names_this_shell():
    desktop = chrome.windows_desktop()

    assert desktop is not None
    assert desktop.count("\\") == 1


def test_heal_restarts_when_cdp_up_but_no_windows_on_user_desktop(monkeypatch, tmp_path: Path, capsys):
    state = {"cdp": [True, False, True], "launches": [], "closed_cdp": False, "killed": []}

    monkeypatch.setattr(chrome.sys, "platform", "win32")
    monkeypatch.setattr(chrome, "get_profile_dir", lambda: tmp_path / "profile")
    monkeypatch.setattr(chrome, "singleton_owner_pid", lambda profile: None)
    monkeypatch.setattr(chrome, "is_cdp_up", lambda: state["cdp"].pop(0) if len(state["cdp"]) > 1 else state["cdp"][0])
    monkeypatch.setattr(chrome, "chrome_windows", lambda desktop=chrome.USER_DESKTOP: [])
    monkeypatch.setattr(chrome, "cdp_close_browser", lambda: state.update({"closed_cdp": True}) or True)
    monkeypatch.setattr(chrome, "launch_chrome", lambda args=None: state["launches"].append(args) or 0)
    monkeypatch.setattr(chrome.time, "sleep", lambda s: None)

    assert chrome.heal_chrome() == 0

    assert state["closed_cdp"] is True
    assert len(state["launches"]) == 1
    err = capsys.readouterr().err
    assert "no top-level windows" in err or "non ha finestre di primo livello" in err


def test_heal_keeps_browser_when_windows_present_on_user_desktop(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(chrome.sys, "platform", "win32")
    monkeypatch.setattr(chrome, "is_cdp_up", lambda: True)
    monkeypatch.setattr(chrome, "chrome_windows", lambda desktop=chrome.USER_DESKTOP: [12345])
    monkeypatch.setattr(chrome, "launch_chrome", lambda args=None: pytest.fail("launch_chrome called"))

    assert chrome.heal_chrome() == 0


def test_heal_graceful_shutdown_windows(monkeypatch, tmp_path: Path):
    run_calls = []
    state = {"cdp": [True, True, False]}

    monkeypatch.setattr(chrome.sys, "platform", "win32")
    monkeypatch.setattr(chrome, "get_profile_dir", lambda: tmp_path / "profile")
    monkeypatch.setattr(chrome, "singleton_owner_pid", lambda profile: None)
    monkeypatch.setattr(chrome, "is_cdp_up", lambda: state["cdp"].pop(0) if len(state["cdp"]) > 1 else state["cdp"][0])
    monkeypatch.setattr(chrome, "chrome_windows", lambda desktop=chrome.USER_DESKTOP: [])
    monkeypatch.setattr(chrome, "cdp_close_browser", lambda: True)
    monkeypatch.setattr(chrome.subprocess, "run", lambda cmd, **kw: run_calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))
    monkeypatch.setattr(chrome, "launch_chrome", lambda args=None: 0)
    monkeypatch.setattr(chrome.time, "sleep", lambda s: None)

    assert chrome.heal_chrome() == 0

    # Stop-Process -Force was NOT called because Chrome exited gracefully
    assert not any("Stop-Process" in " ".join(c) for c in run_calls)


def test_heal_force_shutdown_when_graceful_times_out(monkeypatch, tmp_path: Path):
    run_calls = []

    monkeypatch.setattr(chrome.sys, "platform", "win32")
    monkeypatch.setattr(chrome, "get_profile_dir", lambda: tmp_path / "profile")
    monkeypatch.setattr(chrome, "singleton_owner_pid", lambda profile: None)
    # CDP remains up, simulating hung browser that doesn't close on Browser.close
    monkeypatch.setattr(chrome, "is_cdp_up", lambda: True)
    monkeypatch.setattr(chrome, "chrome_windows", lambda desktop=chrome.USER_DESKTOP: [])
    monkeypatch.setattr(chrome, "cdp_close_browser", lambda: True)
    monkeypatch.setattr(chrome.subprocess, "run", lambda cmd, **kw: run_calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))
    monkeypatch.setattr(chrome, "launch_chrome", lambda args=None: 0)
    monkeypatch.setattr(chrome.time, "sleep", lambda s: None)

    assert chrome.heal_chrome() == 0

    # Stop-Process -Force WAS called after graceful shutdown timed out
    assert any("Stop-Process" in " ".join(c) for c in run_calls)


def test_doctor_check_shared_browser_desktop(monkeypatch):
    from nexgen_core.checks.desktop_checks import check_shared_browser_desktop
    from nexgen_core.report import Severity

    monkeypatch.setattr(sys, "platform", "win32")

    # 1. CDP not up -> None
    monkeypatch.setattr(chrome, "is_cdp_up", lambda: False)
    assert check_shared_browser_desktop() is None

    # 2. CDP up but no windows on WinSta0\Default -> BROKEN
    monkeypatch.setattr(chrome, "is_cdp_up", lambda: True)
    monkeypatch.setattr(chrome, "chrome_windows", lambda desktop=chrome.USER_DESKTOP: [])
    outcome = check_shared_browser_desktop()
    assert outcome is not None
    assert outcome.severity == Severity.BROKEN
    assert outcome.id == "desktop.shared_browser"
    assert "agent-chrome --heal" in (outcome.action or "")

    # 3. CDP up and windows on WinSta0\Default -> OK
    monkeypatch.setattr(chrome, "chrome_windows", lambda desktop=chrome.USER_DESKTOP: [999])
    outcome_ok = check_shared_browser_desktop()
    assert outcome_ok is not None
    assert outcome_ok.severity == Severity.OK

    # 4. Non-win32 -> None
    monkeypatch.setattr(sys, "platform", "linux")
    assert check_shared_browser_desktop() is None

