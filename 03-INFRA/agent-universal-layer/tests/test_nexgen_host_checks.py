"""The doctor says when the timers are not running or a command points at an engine that is gone."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import shims  # noqa: E402
from nexgen_core.checks import host_checks  # noqa: E402
from nexgen_core.report import Severity  # noqa: E402

linux_only = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="systemd")


@pytest.fixture
def systemd(monkeypatch):
    monkeypatch.setattr(host_checks.shutil, "which", lambda name: "/usr/bin/systemctl")
    monkeypatch.setattr("nexgen_core.scheduler.host_mutations_disabled", lambda: False)

    def configure(states):
        monkeypatch.setattr(host_checks, "_systemctl", lambda args: states[(args[0], args[1])])
    return configure


@linux_only
def test_enabled_and_running_timers_are_ok(systemd):
    systemd({(verb, unit): (0, state) for unit in host_checks.TIMERS
             for verb, state in (("is-enabled", "enabled"), ("is-active", "active"))})
    assert host_checks.check_timers_armed().severity == Severity.OK


@linux_only
def test_a_timer_that_is_off_is_named(systemd):
    states = {(verb, unit): (0, state) for unit in host_checks.TIMERS
              for verb, state in (("is-enabled", "enabled"), ("is-active", "active"))}
    states[("is-active", "agent-heartbeat.timer")] = (3, "inactive")
    systemd(states)
    outcome = host_checks.check_timers_armed()
    assert outcome.severity == Severity.BROKEN
    assert "agent-heartbeat.timer" in outcome.message and "agent-sync.timer" not in outcome.message


@linux_only
def test_timers_that_were_never_written_are_broken_too(systemd):
    systemd({(verb, unit): (4, "") for unit in host_checks.TIMERS for verb in ("is-enabled", "is-active")})
    outcome = host_checks.check_timers_armed()
    assert outcome.severity == Severity.BROKEN and "not found" in outcome.message


@linux_only
def test_no_systemd_is_not_a_failure(monkeypatch):
    monkeypatch.setattr(host_checks.shutil, "which", lambda name: None)
    assert host_checks.check_timers_armed().severity == Severity.UNDETERMINED


def test_launchers_pointing_at_this_engine_are_ok_and_a_moved_engine_is_named(tmp_path):
    home = tmp_path / "home"
    shims.install_shims(bin_dir=home / ".local" / "bin", home=home)
    assert host_checks.check_launchers(home).severity == Severity.OK
    victim = home / ".local" / "bin" / ("nexgen.cmd" if sys.platform == "win32" else "nexgen")
    text = victim.read_text(encoding="utf-8")
    entry = host_checks._ENTRY_LINE.search(text).group(1)
    victim.write_text(text.replace(entry, str(tmp_path / "gone" / "cli" / "__init__.py")), encoding="utf-8")
    outcome = host_checks.check_launchers(home)
    assert outcome.severity == Severity.BROKEN
    assert "nexgen" in outcome.message and "gone" in outcome.message


def test_a_script_that_is_not_ours_is_ignored(tmp_path):
    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    (home / ".local" / "bin" / "mine").write_text('#!/bin/sh\nNEXGEN_ENTRY="/nowhere"\n', encoding="utf-8")
    assert host_checks.check_launchers(home).severity == Severity.UNDETERMINED
