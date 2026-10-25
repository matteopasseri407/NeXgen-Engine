"""The Vault is written from one street: the vault-library, never a clone's git.

On a server the vault has more than one copy, and only the worktree the
vault-library serves has a person behind it. The clone the gateway reads is a
reader: it pulls, it parks what it finds, and it never commits on its branch or
pushes. These tests pin that with a real repository and a recorder on every git
command, so "no commit happened" is shown rather than assumed.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from nexgen_core import git_ops, roles, vault_readonly
from nexgen_core.checks.git_checks import check_readonly_clone
from nexgen_core.doctor import _git_checks
from nexgen_core.git_ops import list_quarantine_branches
from nexgen_core.guard_git import converge_git_phase
from nexgen_core.guard_result import GuardMode
from nexgen_core.openclaw.templates import render_workspace
from nexgen_core.report import Severity

pytestmark = pytest.mark.filterwarnings("ignore")

#: The commands a reader must never run: they write history, or ship it.
FORBIDDEN = ("commit", "push", "rebase", "merge")


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False,
    )


def _repository(root: Path) -> tuple[Path, Path]:
    """A bare remote and one clone of it, already holding `04-NOW/nota.md`."""
    remote = root / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True,
                   capture_output=True)
    origin = root / "origin"
    subprocess.run(["git", "clone", str(remote), str(origin)], check=True, capture_output=True)
    _git(origin, "config", "user.email", "test@example.com")
    _git(origin, "config", "user.name", "Test")
    note = origin / "04-NOW" / "nota.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("nota pubblicata\n", encoding="utf-8")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-m", "prima nota")
    _git(origin, "push", "origin", "main")
    return remote, origin


def _reader_clone(remote: Path, where: Path) -> Path:
    subprocess.run(["git", "clone", str(remote), str(where)], check=True, capture_output=True)
    _git(where, "config", "user.email", "test@example.com")
    _git(where, "config", "user.name", "Test")
    return where


def _recorder(monkeypatch) -> list[tuple[str, ...]]:
    """Every git command this test runs, still executed for real."""
    calls: list[tuple[str, ...]] = []
    real = git_ops.run_git

    def record(repo, *args, **kwargs):
        calls.append(tuple(args))
        return real(repo, *args, **kwargs)

    monkeypatch.setattr(git_ops, "run_git", record)
    return calls


def _server_runner(vault: Path, home: Path) -> SimpleNamespace:
    return SimpleNamespace(vault_data=vault, home=home, engine_root=home / "engine")


def _declare_server(home: Path) -> None:
    roles.declare_role(roles.SERVER_ROLE, home)


def test_the_reader_clone_pulls_and_never_commits_or_pushes(tmp_path, monkeypatch):
    remote, origin = _repository(tmp_path)
    home = tmp_path / "home"
    _declare_server(home)
    reader = _reader_clone(remote, tmp_path / "gateway")
    # The served worktree moves on: the reader has to fast-forward to it.
    note = origin / "04-NOW" / "nota.md"
    note.write_text("nota aggiornata\n", encoding="utf-8")
    _git(origin, "commit", "-am", "seconda nota")
    _git(origin, "push", "origin", "main")

    calls = _recorder(monkeypatch)
    actions: list[str] = []
    result = converge_git_phase(
        _server_runner(reader, home), GuardMode.APPLY, False, "origin", "main", actions,
    )

    assert result is None
    assert ("pull", "--ff-only", "origin", "main") in calls
    assert not [c for c in calls if c and c[0] in FORBIDDEN], calls
    assert (reader / "04-NOW" / "nota.md").read_text(encoding="utf-8") == "nota aggiornata\n"


def test_unpublished_commits_are_parked_and_never_lost(tmp_path, monkeypatch):
    remote, origin = _repository(tmp_path)
    home = tmp_path / "home"
    _declare_server(home)
    reader = _reader_clone(remote, tmp_path / "gateway")
    local = reader / "04-NOW" / "nota.md"
    local.write_text("scritta nel clone sbagliato\n", encoding="utf-8")
    _git(reader, "commit", "-am", "nota locale")
    head_before = _git(reader, "rev-parse", "HEAD").stdout.strip()

    actions: list[str] = []
    converge_git_phase(_server_runner(reader, home), GuardMode.APPLY, False, "origin", "main", actions)

    parked = list_quarantine_branches(reader)
    assert len(parked) == 1 and parked[0].startswith("quarantine/diverged-")
    assert _git(reader, "show", f"{parked[0]}:04-NOW/nota.md").stdout.strip() == "scritta nel clone sbagliato"
    # The branch went back to what the remote has; the commit is on the parked branch.
    assert _git(reader, "rev-parse", "HEAD").stdout.strip() != head_before
    assert _git(reader, "rev-list", "--count", "origin/main..HEAD").stdout.strip() == "0"
    assert local.read_text(encoding="utf-8") == "nota pubblicata\n"
    assert any("quarantine" in action for action in actions)


def test_uncommitted_changes_are_parked_and_never_lost(tmp_path, monkeypatch):
    remote, _origin = _repository(tmp_path)
    home = tmp_path / "home"
    _declare_server(home)
    reader = _reader_clone(remote, tmp_path / "gateway")
    tracked = reader / "04-NOW" / "nota.md"
    tracked.write_text("modifica non pubblicata\n", encoding="utf-8")
    untracked = reader / "04-NOW" / "nuova.md"
    untracked.write_text("nota mai committata\n", encoding="utf-8")

    actions: list[str] = []
    converge_git_phase(_server_runner(reader, home), GuardMode.APPLY, False, "origin", "main", actions)

    parked = list_quarantine_branches(reader)
    assert len(parked) == 1, parked
    assert _git(reader, "status", "--porcelain").stdout.strip() == ""
    assert _git(reader, "show", f"{parked[0]}:04-NOW/nota.md").stdout.strip() == "modifica non pubblicata"
    assert _git(reader, "show", f"{parked[0]}:04-NOW/nuova.md").stdout.strip() == "nota mai committata"
    assert tracked.read_text(encoding="utf-8") == "nota pubblicata\n"


def test_an_ordinary_pc_still_commits_its_infra_files(tmp_path, monkeypatch):
    """No role, no vault-library on this machine: the behaviour of every PC is untouched."""
    remote, _origin = _repository(tmp_path)
    home = tmp_path / "home"
    assert vault_readonly.readonly_reason(tmp_path / "gateway", home=home) is None
    clone = _reader_clone(remote, tmp_path / "gateway")
    manifest = clone / "03-INFRA" / "agent-universal-layer" / "mcp" / "manifest.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("servers: {}\n", encoding="utf-8")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-m", "manifest")
    _git(clone, "push", "origin", "main")
    manifest.write_text("servers:\n  vault-library: {}\n", encoding="utf-8")

    calls = _recorder(monkeypatch)
    actions: list[str] = []
    result = converge_git_phase(
        _server_runner(clone, home), GuardMode.APPLY, False, "origin", "main", actions,
    )

    assert result is None
    assert ("pull", "--ff-only", "origin", "main") not in calls
    assert [c for c in calls if c and c[0] == "commit"], calls


def test_a_vault_library_serving_another_checkout_makes_this_clone_a_reader(tmp_path):
    engine = tmp_path / "engine"
    (engine / "deploy").mkdir(parents=True)
    (engine / "deploy" / ".env").write_text("VAULT_WORKTREE_DIR=/srv/knowledge-vault\n", encoding="utf-8")
    vault = tmp_path / "home" / "KnowledgeVault"

    reason = vault_readonly.readonly_reason(vault, home=tmp_path / "home", engine_root=engine)

    assert reason, "a stack serving another worktree means this clone is a second copy"
    assert "vault-library" in reason


def test_the_served_worktree_itself_is_the_writer(tmp_path):
    engine = tmp_path / "engine"
    (engine / "deploy").mkdir(parents=True)
    vault = tmp_path / "knowledge-vault"
    (engine / "deploy" / ".env").write_text(f"VAULT_WORKTREE_DIR={vault}\n", encoding="utf-8")

    assert vault_readonly.readonly_reason(vault, home=tmp_path / "home", engine_root=engine) is None


def test_the_server_role_is_read_back_from_the_machine_and_from_the_old_profile(tmp_path):
    home = tmp_path / "home"
    assert roles.declared_role(home=home) is None
    _declare_server(home)
    assert roles.declared_role(home=home) == roles.SERVER_ROLE

    legacy_home, legacy_vault = tmp_path / "legacy-home", tmp_path / "legacy-vault"
    profile = legacy_vault / "99-INDEX" / "USER-PROFILE.md"
    profile.parent.mkdir(parents=True)
    profile.write_text("- **machines**: `primary (server)`\n", encoding="utf-8")
    assert roles.declared_role(home=legacy_home, vault_root=legacy_vault) == roles.SERVER_ROLE


def test_vault_push_refuses_to_publish_from_the_reader_clone(tmp_path, monkeypatch):
    from nexgen_core import publisher

    home = tmp_path / "home"
    _declare_server(home)
    vault = tmp_path / "gateway"
    vault.mkdir()
    monkeypatch.setenv("NEXGEN_HOME", str(home))

    def never(*_args, **_kwargs):
        raise AssertionError("a reader clone must not reach publish_changes")

    monkeypatch.setattr(publisher, "publish_changes", never)
    code, message = publisher.Publisher(vault_data=vault).publish(message="nota")

    assert code == 1
    assert "vault-library" in message


def test_the_doctor_says_it_in_one_line_and_names_the_command(tmp_path, monkeypatch):
    remote, origin = _repository(tmp_path)
    home = tmp_path / "home"
    _declare_server(home)
    reader = _reader_clone(remote, tmp_path / "gateway")
    (reader / "04-NOW" / "nota.md").write_text("non pubblicata\n", encoding="utf-8")
    _git(reader, "commit", "-am", "locale")

    outcome = check_readonly_clone(
        reader, "the server role owns this machine, and its notes are written with the vault-library",
        expected_branch="main",
    )

    assert outcome.severity is Severity.BROKEN
    assert "1" in outcome.message and "vault-library" in outcome.message
    assert "nexgen pull" in outcome.action


def test_the_doctor_judges_a_reader_clone_as_a_reader(tmp_path):
    remote, _origin = _repository(tmp_path)
    home = tmp_path / "home"
    _declare_server(home)
    reader = _reader_clone(remote, tmp_path / "gateway")
    doc = SimpleNamespace(vault_data=reader, home=home, engine_root=home / "engine")
    produced: dict[str, object] = {}

    _git_checks(lambda check_id, produce, *_a: produced.setdefault(check_id, produce), doc)
    outcome = produced["git.alignment"]()

    # The id is what says which verdict was chosen, whatever language the suite speaks.
    assert outcome.id == "git.readonly_clone"
    assert outcome.severity is Severity.OK


@pytest.mark.parametrize("language", ["it", "en"])
def test_every_agent_manual_carries_the_one_write_path(language):
    for agent in ("main", "assistente", "capo", "automazioni", "sviluppo"):
        text = render_workspace(agent, language)["AGENTS.md"]
        for tool in ("create_note", "append_note", "update_section", "update_note"):
            assert tool in text, (agent, language, tool)
        assert "git commit" in text and "git push" in text, (agent, language)
        assert "vault-library" in text
