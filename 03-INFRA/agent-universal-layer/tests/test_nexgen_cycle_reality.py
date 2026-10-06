"""The guard cycle as the machine actually runs it: twice an hour, forever.

Each component being idempotent on its own did not catch the unit whose PATH grew
51 bytes per cycle: the second cycle starts with the environment the first one
wrote into its own unit. These tests run the whole cycle, scheduler included, and
feed the second run the environment that systemd would give it.
"""
from __future__ import annotations

import os
import re
import stat
from pathlib import Path

import pytest

if os.name == "nt":  # the systemd units, the fake systemctl and the PATH are POSIX
    pytest.skip("systemd-user cycle is Linux-only", allow_module_level=True)

from nexgen_core.guard import GuardMode, GuardRunner


def _fake_systemctl(bin_dir: Path, log: Path) -> None:
    """A systemctl that records its arguments and succeeds. Nothing reaches the real user manager."""
    bin_dir.mkdir(parents=True)
    for name in ("systemctl", "loginctl"):
        script = bin_dir / name
        script.write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\nexit 0\n', encoding="utf-8")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)


def _unit_path(unit: Path) -> str:
    match = re.search(r'^Environment="PATH=(.*)"$', unit.read_text(encoding="utf-8"), re.MULTILINE)
    assert match, unit
    return match.group(1)


@pytest.fixture
def systemd_sandbox(sandbox, monkeypatch, tmp_path):
    """The sandbox with host mutations ON (the scheduler really writes its units into the
    sandbox home) and a fake systemctl first on PATH."""
    log = tmp_path / "systemctl.log"
    fake_bin = tmp_path / "fake-bin"
    _fake_systemctl(fake_bin, log)
    monkeypatch.delenv("NEXGEN_DISABLE_HOST_MUTATIONS", raising=False)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("KNOWLEDGE_VAULT_REMOTE", "local")
    sandbox.systemctl_log = log
    return sandbox


def _cycle(sandbox) -> object:
    return GuardRunner(vault_data=sandbox.vault, home=sandbox.home).run(mode=GuardMode.GUARD, allow_offline=True)


def _tree(sandbox) -> dict:
    return {
        key: value for key, value in sandbox.tree_snapshot().items()
        if not key.replace("\\", "/").startswith(".local/state")
    }


def _systemctl_calls(sandbox) -> list[str]:
    return sandbox.systemctl_log.read_text(encoding="utf-8").splitlines() if sandbox.systemctl_log.exists() else []


def test_the_second_cycle_under_the_services_own_environment_changes_nothing(systemd_sandbox, monkeypatch):
    sandbox = systemd_sandbox
    first = _cycle(sandbox)
    assert first.success, first.message
    unit = sandbox.home / ".config" / "systemd" / "user" / "agent-sync.service"
    assert unit.is_file(), "the scheduler phase did not write the unit: this test would prove nothing"
    heartbeat_unit = unit.with_name("agent-heartbeat.service")
    after_first = _tree(sandbox)
    calls_after_first = _systemctl_calls(sandbox)
    assert any("daemon-reload" in c for c in calls_after_first)  # the first cycle really installed it

    # What systemd does next: start the guard with the PATH its unit declares.
    monkeypatch.setenv("PATH", _unit_path(unit))
    second = _cycle(sandbox)

    assert second.success, second.message
    assert _tree(sandbox) == after_first  # not a byte, not a backup, not a new file
    assert not [c for c in _systemctl_calls(sandbox)[len(calls_after_first):] if "daemon-reload" in c]
    assert _unit_path(unit) == _unit_path(heartbeat_unit)


def test_the_unit_path_stays_the_same_length_over_many_cycles(systemd_sandbox, monkeypatch):
    """The bug itself: +51 bytes per cycle, forever."""
    sandbox = systemd_sandbox
    unit = sandbox.home / ".config" / "systemd" / "user" / "agent-sync.service"
    lengths = []
    for _ in range(5):
        assert _cycle(sandbox).success
        lengths.append(len(_unit_path(unit)))
        monkeypatch.setenv("PATH", _unit_path(unit))
    assert len(set(lengths)) == 1, lengths


def test_a_cycle_leaves_no_backup_files_behind_when_nothing_changed(systemd_sandbox, monkeypatch):
    sandbox = systemd_sandbox
    assert _cycle(sandbox).success
    monkeypatch.setenv("PATH", _unit_path(sandbox.home / ".config" / "systemd" / "user" / "agent-sync.service"))
    backups_before = sorted(str(p) for p in sandbox.home.rglob("*.bak*"))
    assert _cycle(sandbox).success
    assert sorted(str(p) for p in sandbox.home.rglob("*.bak*")) == backups_before
