"""A failed skill update must keep the last usable source and runtime view."""
from __future__ import annotations

import errno
import os
import subprocess
from pathlib import Path

import pytest

from nexgen_core.skill_sources import SkillEntry, SkillFetcher
from nexgen_core.skills import SkillMaterializer


def git(repo, *args):
    # `text=True` alone decodes with the locale codec, and on Windows that
    # decode runs in subprocess's own reader thread: one byte it does not
    # define turns a passing test into an error in a thread. The engine reads
    # every child as UTF-8 with replacement; the tests do the same.
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=True,
    ).stdout.strip()


def source_and_cache(tmp_path):
    repo = tmp_path / "source"
    repo.mkdir()
    git(repo, "init", "--initial-branch=main")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@localhost")
    git(repo, "config", "commit.gpgsign", "false")
    git(repo, "config", "core.hooksPath", str(tmp_path / "empty-hooks"))
    (repo / "SKILL.md").write_text("verified first version\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "first")
    pin = git(repo, "rev-parse", "HEAD")
    cache = tmp_path / "cache"
    git(repo, "clone", str(repo), str(cache))
    entry = SkillEntry("synthetic", origin="github", repo=str(repo), commit=pin)
    return repo, cache, entry, SkillFetcher(home=tmp_path / "home")


def test_a_transient_lock_on_the_swap_is_retried_instead_of_postponed(tmp_path, monkeypatch):
    """Un lock che si libera in millisecondi non deve rimandare il pin al ciclo
    successivo: la prima rinomina del checkout fallisce (WinError 5 / EACCES),
    la seconda porta i byte nuovi e il pin registrato è quello giusto."""
    repo, cache, entry, fetcher = source_and_cache(tmp_path)
    (repo / "SKILL.md").write_text("verified second version\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "second")
    entry.commit = git(repo, "rev-parse", "HEAD")
    real_rename = os.rename
    attempts: list[str] = []

    def flaky_rename(src, dst):
        if Path(src).name == "checkout":
            attempts.append(str(src))
            if len(attempts) == 1:
                raise PermissionError(errno.EACCES, "locked for a moment")
        return real_rename(src, dst)

    monkeypatch.setattr(os, "rename", flaky_rename)
    ok, error = fetcher.ensure_github_checkout(cache, entry)

    assert ok, error
    assert len(attempts) == 2, attempts
    assert git(cache, "rev-parse", "HEAD") == entry.commit
    assert (cache / "SKILL.md").read_text() == "verified second version\n"


def test_unreachable_pin_keeps_previous_skill_bytes(tmp_path):
    repo, cache, entry, fetcher = source_and_cache(tmp_path)
    previous = git(cache, "rev-parse", "HEAD")
    before = (cache / "SKILL.md").read_bytes()
    (repo / "SKILL.md").write_text("new upstream version, not the approved pin\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "upstream moved")
    entry.commit = "f" * 40
    ok, error = fetcher.ensure_github_checkout(cache, entry)
    assert not ok and error
    assert git(cache, "rev-parse", "HEAD") == previous
    assert (cache / "SKILL.md").read_bytes() == before


def test_missing_skill_at_new_pin_keeps_previous_skill(tmp_path):
    repo, cache, entry, fetcher = source_and_cache(tmp_path)
    previous = git(cache, "rev-parse", "HEAD")
    git(repo, "rm", "SKILL.md")
    git(repo, "commit", "-m", "missing entry")
    entry.commit = git(repo, "rev-parse", "HEAD")
    ok, error = fetcher.ensure_github_checkout(cache, entry)
    assert not ok and error
    assert (cache / "SKILL.md").read_text() == "verified first version\n"
    assert git(cache, "rev-parse", "HEAD") == previous


def test_locked_cache_keeps_previous_pin_and_warns(tmp_path, monkeypatch):
    """A cache another process holds open is a retry, not a failure: old
    bytes stay put, the phase stays green, and the pin is not recorded as
    materialized while the files on disk are still the previous ones."""
    repo, cache, entry, fetcher = source_and_cache(tmp_path)
    (repo / "SKILL.md").write_text("upstream moved on\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "upstream moved")
    entry.commit = git(repo, "rev-parse", "HEAD")
    previous = git(cache, "rev-parse", "HEAD")
    real_rename = os.rename

    def locked(src, dst):
        if Path(src) == cache:
            raise PermissionError(errno.EACCES, "locked by another process")
        return real_rename(src, dst)

    monkeypatch.setattr(os, "rename", locked)
    ok, note = fetcher.ensure_github_checkout(cache, entry)
    assert not ok and note
    assert git(cache, "rev-parse", "HEAD") == previous
    assert (cache / "SKILL.md").read_text() == "verified first version\n"


def test_changed_bytes_at_same_pin_are_never_reported_as_verified(tmp_path):
    _repo, cache, entry, fetcher = source_and_cache(tmp_path)
    (cache / "SKILL.md").write_text("local edit, preserve it\n")
    ok, error = fetcher.ensure_github_checkout(cache, entry)
    assert not ok and error
    assert (cache / "SKILL.md").read_text() == "local edit, preserve it\n"


def test_valid_pin_bump_changes_bytes_and_exact_head(tmp_path):
    repo, cache, entry, fetcher = source_and_cache(tmp_path)
    (repo / "SKILL.md").write_text("verified second version\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "second")
    entry.commit = git(repo, "rev-parse", "HEAD")
    ok, error = fetcher.ensure_github_checkout(cache, entry)
    assert ok, error
    assert git(cache, "rev-parse", "HEAD") == entry.commit
    assert (cache / "SKILL.md").read_text() == "verified second version\n"
    assert fetcher.ensure_github_checkout(cache, entry) == (True, None)


def test_activation_failure_restores_previous_skill(tmp_path, monkeypatch):
    repo, cache, entry, fetcher = source_and_cache(tmp_path)
    previous = git(cache, "rev-parse", "HEAD")
    (repo / "SKILL.md").write_text("verified second version\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "second")
    entry.commit = git(repo, "rev-parse", "HEAD")
    original = Path.rename

    def rename(path, target):
        if path.name == "checkout" and target == cache:
            raise PermissionError("synthetic activation failure")
        return original(path, target)

    monkeypatch.setattr(Path, "rename", rename)
    ok, error = fetcher.ensure_github_checkout(cache, entry)
    assert not ok and error
    assert git(cache, "rev-parse", "HEAD") == previous
    assert (cache / "SKILL.md").read_text() == "verified first version\n"


@pytest.mark.parametrize("subpath", ["../outside", "missing"])
def test_invalid_declared_source_never_replaces_usable_cache(tmp_path, subpath):
    _repo, cache, entry, fetcher = source_and_cache(tmp_path)
    entry.path = subpath
    ok, error = fetcher.ensure_github_checkout(cache, entry)
    assert not ok and error
    assert (cache / "SKILL.md").read_text() == "verified first version\n"


def test_a_locked_cache_does_not_record_the_new_pin(tmp_path, monkeypatch):
    """MUST-07: un lock sulla cache è un rinvio, ma il pin non deve essere
    registrato: i byte nuovi non ci sono, e il doctor crederebbe di sì."""
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "--initial-branch=main")
    git(source, "config", "user.name", "Test")
    git(source, "config", "user.email", "test@localhost")
    git(source, "config", "commit.gpgsign", "false")
    git(source, "config", "core.hooksPath", str(tmp_path / "empty-hooks"))
    (source / "SKILL.md").write_text("verified first version\n")
    git(source, "add", ".")
    git(source, "commit", "-m", "first")
    old_pin = git(source, "rev-parse", "HEAD")

    mat = SkillMaterializer(vault_data=tmp_path / "vault", home=tmp_path / "home")
    cache_dir = mat.home / ".agents" / "cache" / "github-skills" / "synthetic"
    cache_dir.parent.mkdir(parents=True, exist_ok=True)
    git(tmp_path, "clone", str(source), str(cache_dir))
    mat.fetcher._record_installed_version("synthetic", old_pin)
    previous = mat.fetcher._installed_versions_file().read_bytes()

    (source / "SKILL.md").write_text("verified second version\n")
    git(source, "add", ".")
    git(source, "commit", "-m", "second")
    entry = SkillEntry("synthetic", origin="github", repo=str(source),
                       commit=git(source, "rev-parse", "HEAD"))

    real_rename = os.rename

    def locked(src, dst):
        if Path(src) == cache_dir:
            raise PermissionError(errno.EACCES, "locked by another process")
        return real_rename(src, dst)

    monkeypatch.setattr(os, "rename", locked)
    monkeypatch.setattr(mat, "load_manifest", lambda: {"synthetic": entry})
    _, actions = mat.materialize(apply=True)

    assert any("locked" in action for action in actions), actions
    assert mat.fetcher._installed_versions_file().read_bytes() == previous
