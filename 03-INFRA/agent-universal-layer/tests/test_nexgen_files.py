"""One way to write files: every policy pinned, not just documented."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.files import atomic_write_text, backup_file, write_text_if_changed  # noqa: E402


def test_atomic_write_creates_parents_and_replaces(tmp_path: Path):
    target = tmp_path / "sub" / "cfg.json"
    atomic_write_text(target, '{"a": 1}')
    assert target.read_text(encoding="utf-8") == '{"a": 1}'
    atomic_write_text(target, '{"a": 2}')
    assert target.read_text(encoding="utf-8") == '{"a": 2}'
    assert list(tmp_path.rglob("*.tmp*")) == []


def test_atomic_write_preserves_mode(tmp_path: Path):
    if os.name == "nt":
        pytest.skip("mode bits are a POSIX concept; the writer still works")
    target = tmp_path / "secret.env"
    target.write_text("x", encoding="utf-8")
    os.chmod(target, 0o600)
    atomic_write_text(target, "y")
    assert target.read_text(encoding="utf-8") == "y"
    assert target.stat().st_mode & 0o777 == 0o600


def test_atomic_write_over_a_read_only_target_survives_windows_refusals(tmp_path: Path, monkeypatch):
    """Ricognizione 13: su Windows un file read-only non si sostituisce e non
    si cancella; il temporaneo read-only restava orfano e mascherava l'errore
    originale. Il rifiuto di Windows è simulato qui, così la prova gira anche
    su Linux."""
    import nexgen_core.files as files_mod

    target = tmp_path / "cfg.json"
    target.write_text("old", encoding="utf-8")
    os.chmod(target, 0o444)

    real_replace = os.replace
    real_unlink = Path.unlink

    def refusing_replace(src, dst):
        for entry in (Path(src), Path(dst)):
            if entry.exists() and not entry.stat().st_mode & 0o200:
                raise PermissionError(13, "Access denied: read-only", str(entry))
        return real_replace(src, dst)

    def refusing_unlink(self, *args, **kwargs):
        if self.exists() and not self.stat().st_mode & 0o200:
            raise PermissionError(13, "Access denied: read-only", str(self))
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(files_mod.os, "replace", refusing_replace)
    monkeypatch.setattr(Path, "unlink", refusing_unlink)
    try:
        files_mod.atomic_write_text(target, "new")
    finally:
        monkeypatch.setattr(Path, "unlink", real_unlink)

    assert target.read_text(encoding="utf-8") == "new"
    assert target.stat().st_mode & 0o200 == 0, "il file pubblicato resta read-only"
    assert list(tmp_path.rglob("*.tmp*")) == [], "il temporaneo non deve restare orfano"


def test_backup_names_reasons_and_skips_missing(tmp_path: Path):
    assert backup_file(tmp_path / "absent.json") is None
    target = tmp_path / "cfg.json"
    target.write_text("v1", encoding="utf-8")
    plain = backup_file(target)
    assert plain is not None and plain.name.startswith("cfg.json.bak-")
    tagged = backup_file(target, tag="permissions")
    assert tagged is not None and ".pre-permissions-" in tagged.name
    assert tagged.read_text(encoding="utf-8") == "v1"


def test_backup_rotation_keeps_only_keep(tmp_path: Path):
    target = tmp_path / "cfg.json"
    target.write_text("v", encoding="utf-8")
    import time as _time

    for _ in range(4):
        backup_file(target, keep=2)
        _time.sleep(1.05)
    survivors = sorted(tmp_path.glob("cfg.json.bak-*"))
    assert len(survivors) == 2, [p.name for p in survivors]


def test_backup_without_keep_accumulates(tmp_path: Path):
    target = tmp_path / "cfg.json"
    target.write_text("v", encoding="utf-8")
    import time as _time

    for _ in range(2):
        backup_file(target)
        _time.sleep(1.05)
    assert len(list(tmp_path.glob("cfg.json.bak-*"))) == 2


def _git_repo_with_commit(repo: Path, name: str = "cfg.json") -> Path:
    import shutil
    import subprocess

    if shutil.which("git") is None:
        pytest.skip("git non disponibile")
    target = repo / name
    target.write_text("v1", encoding="utf-8")
    env = dict(
        os.environ,
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@t",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@t",
    )
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, env=env)
    subprocess.run(["git", "add", name], cwd=repo, check=True, env=env)
    subprocess.run(["git", "commit", "-qm", "v1"], cwd=repo, check=True, env=env)
    return target


def test_backup_skips_committed_clean_file(tmp_path: Path):
    target = _git_repo_with_commit(tmp_path)
    assert backup_file(target, skip_if_committed=True) is None
    assert list(tmp_path.glob("cfg.json.bak-*")) == []
    # Senza opt-in il comportamento resta quello storico.
    assert backup_file(target) is not None


def test_backup_keeps_dirty_and_untracked(tmp_path: Path):
    target = _git_repo_with_commit(tmp_path)
    target.write_text("v2-sporca", encoding="utf-8")
    bak = backup_file(target, skip_if_committed=True)
    assert bak is not None and bak.read_text(encoding="utf-8") == "v2-sporca"
    fresh = tmp_path / "nuovo.json"
    fresh.write_text("x", encoding="utf-8")
    assert backup_file(fresh, skip_if_committed=True) is not None


def test_backup_skips_only_outside_repos(tmp_path: Path):
    target = tmp_path / "cfg.json"
    target.write_text("v", encoding="utf-8")
    assert backup_file(target, skip_if_committed=True) is not None


def test_write_if_changed_skips_backup_when_committed(tmp_path: Path):
    target = _git_repo_with_commit(tmp_path)
    assert write_text_if_changed(target, "v2", keep=3, skip_if_committed=True) is True
    assert target.read_text(encoding="utf-8") == "v2"
    assert list(tmp_path.glob("cfg.json.bak-*")) == []


def test_write_if_changed_skips_identical(tmp_path: Path):
    target = tmp_path / "cfg.json"
    target.write_text("same", encoding="utf-8")
    assert write_text_if_changed(target, "same", keep=3) is False
    assert list(tmp_path.glob("*.bak-*")) == []
    assert write_text_if_changed(target, "new", keep=3) is True
    assert target.read_text(encoding="utf-8") == "new"
    assert len(list(tmp_path.glob("*.bak-*"))) == 1


def test_skip_if_committed_judges_the_file_actually_written(tmp_path: Path, monkeypatch):
    """A committed, clean symlink said "no backup needed" while the real file
    it points to had uncommitted edits; the write then overwrote them."""
    import shutil
    import subprocess

    import nexgen_core.files as files_mod

    target = _git_repo_with_commit(tmp_path, name="real.json")
    # link.json is a committed regular file here; the symlink is simulated by
    # making the module's Path report it as a link to real.json. This needs no
    # symlink privilege, so the test runs on every platform.
    link = tmp_path / "link.json"
    link.write_text("v1", encoding="utf-8")
    env = dict(
        os.environ,
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@t",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@t",
    )
    subprocess.run(["git", "add", "link.json"], cwd=tmp_path, check=True, env=env)
    subprocess.run(["git", "commit", "-qm", "link"], cwd=tmp_path, check=True, env=env)
    target.write_text("v2-sporca", encoding="utf-8")

    class _LinkedPath(type(Path())):
        def is_symlink(self):
            return self.name == "link.json" or super().is_symlink()

        def resolve(self, strict=False):
            if self.name == "link.json":
                return target
            return super().resolve(strict)

    real_copy2 = shutil.copy2

    def _copy2_following_link(src, dst, *args, **kwargs):
        # copy2 follows a real symlink, so the backup holds the target's bytes.
        source = target if Path(src).name == "link.json" else src
        return real_copy2(source, dst, *args, **kwargs)

    monkeypatch.setattr(files_mod, "Path", _LinkedPath)
    monkeypatch.setattr(shutil, "copy2", _copy2_following_link)
    assert files_mod.write_text_if_changed(link, "v3", skip_if_committed=True) is True
    assert target.read_text(encoding="utf-8") == "v3"
    backups = list(tmp_path.glob("link.json.bak-*"))
    assert [b.read_text(encoding="utf-8") for b in backups] == ["v2-sporca"]


def test_backup_of_a_loosely_moded_config_is_private(tmp_path: Path):
    """A backup copied the source's mode, so a 0644 config that held resolved tokens
    left a readable 0644 copy behind. Backups are 0600 on POSIX whatever the source."""
    if os.name == "nt":
        pytest.skip("mode bits are a POSIX concept")
    target = tmp_path / "config.json"
    target.write_text('{"token": "secret"}', encoding="utf-8")
    os.chmod(target, 0o644)
    backup = backup_file(target, tag="instructions")
    assert backup is not None and backup.stat().st_mode & 0o777 == 0o600
