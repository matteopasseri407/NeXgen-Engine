"""The private vault is never published to the engine's public repository."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core import git_ops  # noqa: E402
from nexgen_core.checks.git_checks import check_vault_remote_privacy  # noqa: E402
from nexgen_core.report import Severity  # noqa: E402

ENGINE_HTTPS = "https://github.com/matteopasseri407/NeXgen-Engine.git"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, encoding="utf-8")


@pytest.fixture
def vault(tmp_path):
    repo = tmp_path / "vault"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "note.md").write_text("# private\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed")
    return repo


@pytest.mark.parametrize("url,expected", [
    ("https://github.com/Owner/Repo.git", "github.com/owner/repo"),
    ("https://x-access-token@example.com/Owner/Repo/", "example.com/owner/repo"),
    ("git@github.com:Owner/Repo.git", "github.com/owner/repo"),
    ("ssh://git@github.com:22/Owner/Repo", "github.com/owner/repo"),
    ("vault-host:/opt/shared/knowledge-vault", "vault-host/opt/shared/knowledge-vault"),
])
def test_every_spelling_of_a_repository_normalizes_alike(url, expected):
    assert git_ops.normalize_remote_url(url) == expected


@pytest.mark.parametrize("url", [
    ENGINE_HTTPS,
    "git@github.com:matteopasseri407/NeXgen-Engine.git",
    "ssh://git@github.com/MatteoPasseri407/nexgen-engine",
])
def test_publishing_to_the_engine_repository_is_refused_before_any_network_call(vault, url, monkeypatch):
    _git(vault, "remote", "add", "origin", url)
    (vault / "note.md").write_text("# private, edited\n", encoding="utf-8")
    seen = []
    real = git_ops.run_git
    monkeypatch.setattr(git_ops, "run_git", lambda repo, *a, **k: seen.append(a[0]) or real(repo, *a, **k))
    ok, message = git_ops.publish_changes(vault, "main", "origin", commit_msg="edit", files_to_commit=["note.md"])
    assert ok is False and url in message  # the wording follows the locale; the address does not
    assert "fetch" not in seen and "push" not in seen
    assert _head_subject(vault) == "edit"  # the work itself is committed locally


def _head_subject(repo: Path) -> str:
    return subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%s"], capture_output=True, text=True,
                          encoding="utf-8", check=True).stdout.strip()


def test_a_mirror_that_is_the_engine_repository_stops_the_publication_too(vault, monkeypatch):
    _git(vault, "remote", "add", "origin", str(vault.parent / "elsewhere.git"))
    _git(vault, "remote", "add", "backup", ENGINE_HTTPS)
    seen = []
    real = git_ops.run_git
    monkeypatch.setattr(git_ops, "run_git", lambda repo, *a, **k: seen.append(a[0]) or real(repo, *a, **k))
    ok, message = git_ops.publish_changes(vault, "main", "origin", mirrors=["backup"])
    assert ok is False and "backup" in message
    assert "push" not in seen


def test_the_push_address_counts_even_when_the_fetch_address_is_innocent(vault):
    _git(vault, "remote", "add", "origin", str(vault.parent / "private.git"))
    _git(vault, "remote", "set-url", "--push", "origin", ENGINE_HTTPS)
    assert [name for name, _ in git_ops.engine_upstream_remotes(vault)] == ["origin"]


def test_a_forks_own_repository_can_be_declared(vault, monkeypatch):
    _git(vault, "remote", "add", "origin", "https://example.com/Team/Fork.git")
    assert git_ops.engine_upstream_remotes(vault) == []
    monkeypatch.setenv("NEXGEN_ENGINE_UPSTREAMS", "example.com/team/fork")
    assert [n for n, _ in git_ops.engine_upstream_remotes(vault)] == ["origin"]


def test_an_ordinary_private_remote_is_left_alone(vault):
    _git(vault, "remote", "add", "origin", "vault-host:/opt/shared-agent-library/knowledge-vault")
    assert git_ops.engine_upstream_remotes(vault) == []


def test_the_doctor_names_the_remote_and_says_ok_otherwise(vault):
    assert check_vault_remote_privacy(vault).severity == Severity.OK
    _git(vault, "remote", "add", "origin", ENGINE_HTTPS)
    outcome = check_vault_remote_privacy(vault)
    assert outcome.severity == Severity.BROKEN
    assert "origin" in outcome.message and ENGINE_HTTPS in outcome.message


def test_a_directory_that_is_not_a_repository_is_not_this_checks_business(tmp_path):
    assert check_vault_remote_privacy(tmp_path) is None
