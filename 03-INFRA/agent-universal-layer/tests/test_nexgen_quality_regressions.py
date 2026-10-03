"""Failures must preserve the previous usable state, not merely return an error."""

from __future__ import annotations

import os
import sys
import time

import pytest

from nexgen_core import files
from nexgen_core.guard import GuardMode, GuardRunner
from nexgen_core.lock import HostLock
from nexgen_core.skill_sources import SkillEntry, SkillFetcher


def test_atomic_write_preserves_foreign_temporary_file(tmp_path):
    target = tmp_path / "config.json"
    foreign = tmp_path / "config.json.manual.tmp"
    foreign.write_text("user draft")
    os.utime(foreign, (time.time() - 600, time.time() - 600))
    files.atomic_write_text(target, "new")
    assert foreign.read_text() == "user draft"


def test_backups_keep_both_versions_with_a_frozen_clock(tmp_path, monkeypatch):
    monkeypatch.setattr(files.time, "strftime", lambda *_: "20261003-120000")
    target = tmp_path / "config.json"
    target.write_text("first")
    first = files.backup_file(target)
    target.write_text("second")
    second = files.backup_file(target)
    assert first != second
    assert first.read_text() == "first"
    assert second.read_text() == "second"


def test_failed_atomic_replace_preserves_original_and_cleans_own_temp(tmp_path, monkeypatch):
    target = tmp_path / "config.json"
    target.write_text("old")

    def fail(*_):
        raise OSError("disk error")

    monkeypatch.setattr(files.os, "replace", fail)
    with pytest.raises(OSError):
        files.atomic_write_text(target, "new")
    assert target.read_text() == "old"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["config.json"]


@pytest.mark.parametrize("operation", ["stat", "unlink"])
def test_atomic_write_retries_transient_metadata_and_cleanup_denials(tmp_path, monkeypatch, operation):
    from pathlib import Path

    target = tmp_path / "config.json"
    target.write_text("old")
    real = getattr(Path, operation)
    denials = []
    attempts = []

    def transient(path, *args, **kwargs):
        affected = path == target if operation == "stat" else path.name.startswith("config.json.")
        if affected:
            attempts.append(path)
        if affected and not denials:
            denials.append(path)
            raise PermissionError("transient file lock")
        return real(path, *args, **kwargs)

    monkeypatch.setattr(Path, operation, transient)
    files.atomic_write_text(target, "new")
    assert denials
    assert len(attempts) > 1
    assert target.read_text() == "new"
    assert list(tmp_path.iterdir()) == [target]


def test_atomic_write_refuses_unknown_permissions_before_publication(tmp_path, monkeypatch):
    from pathlib import Path

    target = tmp_path / "config.json"
    target.write_text("old")
    real_stat = Path.stat
    attempts = []
    clock = [0.0]
    monkeypatch.setattr(files.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(files.time, "sleep", lambda delay: clock.__setitem__(0, clock[0] + delay))

    def locked(path, *args, **kwargs):
        if path == target:
            attempts.append(path)
            raise PermissionError("persistent file lock")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", locked)
    with pytest.raises(PermissionError):
        files.atomic_write_text(target, "new")
    assert len(attempts) > 1
    assert clock[0] <= files._RETRY_BUDGET_SECONDS
    assert target.read_text() == "old"
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.parametrize("linked", [False, True])
def test_failed_skill_replacement_keeps_active_views(tmp_path, monkeypatch, linked):
    import nexgen_core.skill_sources as sources

    library = tmp_path / "library" / "demo"
    old = tmp_path / "old"
    old.mkdir()
    (old / "SKILL.md").write_text("old")
    library.parent.mkdir()
    if linked:
        try:
            library.symlink_to(old, target_is_directory=True)
        except OSError:
            pytest.skip("host cannot create directory symlinks")
    else:
        sources.shutil.copytree(old, library)
    discovery = tmp_path / "discovery"
    candidate = discovery / "demo"
    candidate.mkdir(parents=True)
    (candidate / "SKILL.md").write_text("new")

    def fail(*_, **__):
        raise OSError("replacement failed")

    monkeypatch.setattr(sources.shutil, "move", fail)
    assert not SkillFetcher(home=tmp_path).claim_from_discovery("demo", library, (discovery,))
    assert (library / "SKILL.md").read_text() == "old"
    assert (candidate / "SKILL.md").read_text() == "new"


def test_noop_installer_cannot_certify_stale_symlink(tmp_path):
    old = tmp_path / "old"
    old.mkdir()
    (old / "SKILL.md").write_text("old")
    library = tmp_path / "library"
    try:
        library.symlink_to(old, target_is_directory=True)
    except OSError:
        pytest.skip("host cannot create directory symlinks")
    fetcher = SkillFetcher(home=tmp_path)
    entry = SkillEntry(name="demo", origin="installer", version="2", install=[sys.executable, "-c", "pass"])
    ok, _ = fetcher.install_third_party(entry, library, ())
    assert not ok
    assert fetcher._installed_versions().get("demo") != "2"


def test_lock_io_failure_is_not_guard_contention(tmp_path):
    parent = tmp_path / "file"
    parent.write_text("not a directory")
    with pytest.raises(Exception) as error:
        HostLock(lock_path=parent / "lock", timeout=0, is_guard=True).acquire()
    assert getattr(error.value, "exit_code", 1) != 0


def test_skill_failure_stops_guard_before_runtime_writes(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    runner = GuardRunner(vault_data=vault, home=tmp_path / "home")
    monkeypatch.setattr(runner, "_phase_git", lambda *_: None)
    monkeypatch.setattr(runner, "_phase_preflight", lambda *_: None)
    monkeypatch.setattr(
        "nexgen_core.guard.SkillMaterializer.materialize", lambda *_a, **_k: (0, ["[ERROR] failed skill"])
    )
    called = []
    monkeypatch.setattr(runner, "_phase_mcp", lambda *_: called.append("mcp"))
    result = runner.run(GuardMode.APPLY)
    assert not result.success
    assert result.exit_code != 0
    assert not called
    assert "[ERROR] failed skill" in result.actions_taken
    assert not runner.heartbeat.liveness_file.exists()


@pytest.mark.parametrize(
    "module,directory",
    [
        ("compose", "mails_dir"),
        ("calendars", "calendars_dir"),
        ("workflows", "workflows_dir"),
        ("patch", "proposals_dir"),
    ],
)
def test_proposal_update_failure_preserves_reviewed_content(tmp_path, monkeypatch, module, directory):
    from dataclasses import dataclass
    from importlib import import_module
    from nexgen_local.config import LaneConfig

    @dataclass
    class Proposal:
        id: str = "synthetic"
        body: str = "new"

    path = tmp_path / module
    path.mkdir()
    target = path / "synthetic.json"
    target.write_text("reviewed")
    cfg = LaneConfig(vault_root=tmp_path, **{directory: path})

    def fail(*_):
        raise OSError("disk full")

    monkeypatch.setattr(files.os, "fsync", fail)
    with pytest.raises(OSError):
        import_module("nexgen_local." + module)._save(cfg, Proposal())
    assert target.read_text() == "reviewed"


def test_exclusive_atomic_creation_preserves_existing_artifact(tmp_path):
    target = tmp_path / "proposal.json"
    files.atomic_write_text(target, "reviewed", exclusive=True)
    with pytest.raises(FileExistsError):
        files.atomic_write_text(target, "different", exclusive=True)
    assert target.read_text() == "reviewed"
    assert list(tmp_path.iterdir()) == [target]


@pytest.mark.skipif(os.name == 'nt', reason='POSIX access permissions')
def test_private_writer_establishes_permissions_before_publication(tmp_path, monkeypatch):
    target = tmp_path / 'private' / 'draft.json'
    target.parent.mkdir(mode=0o755)
    target.write_text('old')
    target.chmod(0o644)
    real_replace = files.os.replace
    def publish(source, destination):
        assert target.parent.stat().st_mode & 0o777 == 0o700
        assert os.stat(source).st_mode & 0o777 == 0o600
        return real_replace(source, destination)
    monkeypatch.setattr(files.os, 'replace', publish)
    files.write_private_text(target, 'private')
    assert target.read_text() == 'private'
    assert target.stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(os.name == 'nt', reason='POSIX access permissions')
def test_private_directory_permission_error_stops_before_write(tmp_path, monkeypatch):
    target = tmp_path / 'private' / 'draft.json'
    def fail(*_):
        raise PermissionError('cannot protect directory')
    monkeypatch.setattr(files.os, 'chmod', fail)
    with pytest.raises(PermissionError):
        files.write_private_text(target, 'private')
    assert not target.exists()


def test_lock_open_error_is_not_guard_contention(tmp_path, monkeypatch):
    from nexgen_core.lock import LockIOError
    def fail(*_):
        raise OSError('disk full')
    monkeypatch.setattr('nexgen_core.lock.os.open', fail)
    with pytest.raises(LockIOError):
        HostLock(lock_path=tmp_path / 'lock', timeout=0, is_guard=True).acquire()


def test_backup_rotation_preserves_other_files_and_tags(tmp_path):
    target = tmp_path / '[config].json'
    target.write_text('first')
    foreign = tmp_path / 'c.json.bak-foreign'
    foreign.write_text('user backup')
    first = files.backup_file(target, keep=1)
    other = files.backup_file(target, tag='other', keep=1)
    target.write_text('second')
    second = files.backup_file(target, keep=1)
    assert not first.exists()
    assert second.read_text() == 'second'
    assert other.read_text() == 'first'
    assert foreign.read_text() == 'user backup'
