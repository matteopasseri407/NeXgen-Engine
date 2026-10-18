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


def test_a_credential_in_a_remote_url_never_reaches_a_message(vault, monkeypatch):
    """The doctor prints the remote address and the alert channel forwards it: a token
    pasted into the URL stays out of both, while the address stays readable."""
    monkeypatch.delenv("KNOWLEDGE_VAULT_REMOTE", raising=False)
    monkeypatch.delenv("KNOWLEDGE_VAULT_MIRRORS", raising=False)
    token = "ghp_" + "A" * 36
    _git(vault, "remote", "add", "origin",
         f"https://x-access-token:{token}@github.com/matteopasseri407/NeXgen-Engine.git")

    outcome = check_vault_remote_privacy(vault)

    assert outcome.severity == Severity.BROKEN
    assert token not in outcome.message and "x-access-token" not in outcome.message
    assert "github.com/matteopasseri407/nexgen-engine" in outcome.message.lower()

    (vault / "note.md").write_text("# private, edited\n", encoding="utf-8")
    ok, message = git_ops.publish_changes(
        vault, "main", "origin", commit_msg="edit", files_to_commit=["note.md"],
    )
    assert ok is False
    assert token not in message and "x-access-token" not in message


def _write_remotes(vault, text):
    sync_dir = vault / "03-INFRA" / "agent-universal-layer" / "sync"
    sync_dir.mkdir(parents=True, exist_ok=True)
    (sync_dir / "remotes.yaml").write_text(text, encoding="utf-8")


def test_locked_single_clone_is_silent(vault, monkeypatch):
    """Standard setup: public origin is fetch-only, publishing goes private."""
    monkeypatch.delenv("KNOWLEDGE_VAULT_REMOTE", raising=False)
    monkeypatch.delenv("KNOWLEDGE_VAULT_MIRRORS", raising=False)
    _git(vault, "remote", "add", "origin", ENGINE_HTTPS)
    _git(vault, "remote", "set-url", "--push", "origin", "DISABLED")
    _write_remotes(vault, "authoritative_remote: vault\n")
    assert check_vault_remote_privacy(vault).severity == Severity.OK


def test_pushable_public_remote_is_a_calm_warning_not_a_panic(vault, monkeypatch):
    monkeypatch.delenv("KNOWLEDGE_VAULT_REMOTE", raising=False)
    monkeypatch.delenv("KNOWLEDGE_VAULT_MIRRORS", raising=False)
    _git(vault, "remote", "add", "origin", ENGINE_HTTPS)
    _write_remotes(vault, "authoritative_remote: vault\n")
    outcome = check_vault_remote_privacy(vault)
    assert outcome.severity == Severity.WARN
    assert "origin" in outcome.message
    assert "refus" not in (outcome.message + (outcome.action or "")).lower()


def test_public_authoritative_remote_stays_broken(vault, monkeypatch):
    monkeypatch.delenv("KNOWLEDGE_VAULT_REMOTE", raising=False)
    monkeypatch.delenv("KNOWLEDGE_VAULT_MIRRORS", raising=False)
    _git(vault, "remote", "add", "origin", ENGINE_HTTPS)
    _write_remotes(vault, "authoritative_remote: origin\n")
    assert check_vault_remote_privacy(vault).severity == Severity.BROKEN


def test_bare_push_to_public_stays_broken_with_a_fix_that_keeps_updates(vault, monkeypatch):
    """A clone tracking the public remote would publish on a bare push or an
    IDE sync: loud, but the fix must keep fetching updates, not move origin."""
    monkeypatch.delenv("KNOWLEDGE_VAULT_REMOTE", raising=False)
    monkeypatch.delenv("KNOWLEDGE_VAULT_MIRRORS", raising=False)
    _git(vault, "remote", "add", "origin", ENGINE_HTTPS)
    _git(vault, "config", "branch.main.remote", "origin")
    _write_remotes(vault, "authoritative_remote: vault\n")
    outcome = check_vault_remote_privacy(vault)
    assert outcome.severity == Severity.BROKEN
    assert "DISABLED" in (outcome.action or "")


def test_local_mode_is_silent(vault, monkeypatch):
    monkeypatch.delenv("KNOWLEDGE_VAULT_REMOTE", raising=False)
    monkeypatch.delenv("KNOWLEDGE_VAULT_MIRRORS", raising=False)
    _git(vault, "remote", "add", "origin", ENGINE_HTTPS)
    _write_remotes(vault, "authoritative_remote: local\n")
    assert check_vault_remote_privacy(vault).severity == Severity.OK


def test_second_push_url_is_honored(vault, monkeypatch):
    """One DISABLED push URL is not enough when a second one stays public."""
    monkeypatch.delenv("KNOWLEDGE_VAULT_REMOTE", raising=False)
    monkeypatch.delenv("KNOWLEDGE_VAULT_MIRRORS", raising=False)
    _git(vault, "remote", "add", "origin", ENGINE_HTTPS)
    _git(vault, "remote", "set-url", "--push", "origin", "DISABLED")
    _git(vault, "remote", "set-url", "--add", "--push", "origin",
          "git@github.com:matteopasseri407/NeXgen-Engine.git")
    _write_remotes(vault, "authoritative_remote: vault\n")
    assert check_vault_remote_privacy(vault).severity == Severity.WARN


def test_unreadable_push_config_fails_safe_to_warning(vault, monkeypatch):
    import nexgen_core.checks.git_checks as git_checks_mod
    from types import SimpleNamespace

    monkeypatch.delenv("KNOWLEDGE_VAULT_REMOTE", raising=False)
    monkeypatch.delenv("KNOWLEDGE_VAULT_MIRRORS", raising=False)
    _git(vault, "remote", "add", "origin", ENGINE_HTTPS)
    _write_remotes(vault, "authoritative_remote: vault\n")
    real = git_checks_mod.run_git

    def broken(repo, *args, **kwargs):
        if list(args[:3]) == ["remote", "get-url", "--push"]:
            return SimpleNamespace(returncode=128, stdout="", stderr="boom")
        return real(repo, *args, **kwargs)

    monkeypatch.setattr(git_checks_mod, "run_git", broken)
    assert check_vault_remote_privacy(vault).severity == Severity.WARN
