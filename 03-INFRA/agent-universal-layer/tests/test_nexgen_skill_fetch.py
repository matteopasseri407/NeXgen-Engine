"""A failed skill update must keep the last usable source and runtime view."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from nexgen_core.skill_sources import SkillEntry, SkillFetcher


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


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
