"""La copia di lavoro di un'unità: ramo locale, confine scritto, push che non funziona.

Repository git veri in cartelle temporanee, remoto `origin` vero e nudo: nessun
finto, perché quello che si prova sono proprio i fatti di git.
"""
from __future__ import annotations

import json

import pytest

from nexgen_local import supervision_state as state
from nexgen_local import supervision_worktree as worktree

from supervision_repo import build, git_in


@pytest.fixture
def lane(tmp_path, monkeypatch):
    """La lane vive in una cartella temporanea: nessun test scrive nello stato vero."""
    monkeypatch.setattr(state, "lane_state_dir", lambda: tmp_path / "lane")
    return tmp_path / "lane" / "supervision"


@pytest.fixture
def repo(tmp_path):
    return build(tmp_path / "sandbox")


@pytest.fixture
def task(lane):
    return state.start("prova fase 2")["id"]


def _brief(tmp_path) -> str:
    path = tmp_path / "brief.md"
    path.write_text("# Brief dell'unità prova\n\nSolo fatti.\n", encoding="utf-8")
    return str(path)


def _create(task, repo, tmp_path, **overrides):
    params = {
        "repo": repo.path,
        "base": "main",
        "brief": _brief(tmp_path),
        "allow": ["docs/*.md"],
        "verify": 'python -c "print(\'ok\')"',
    }
    params.update(overrides)
    return worktree.create(task, "prova", **params)


def test_create_prepares_the_copy_the_branch_and_the_written_environment(task, repo, tmp_path, lane):
    record = _create(task, repo, tmp_path)

    folder = lane / task / "23-unita-prova"
    assert record["worktree"] == str(folder)
    assert record["branch"] == f"sup/{task}/prova"
    assert folder.is_dir()
    assert (folder / "docs" / "guida.md").is_file()
    assert repo.git("branch", "--list", record["branch"]).strip().endswith("prova")

    assert (lane / task / "20-brief-prova.md").read_text(encoding="utf-8").startswith("# Brief")
    ambiente = (lane / task / "22-ambiente-prova.md").read_text(encoding="utf-8")
    assert str(folder) in ambiente
    assert record["branch"] in ambiente
    assert "docs/*.md" in ambiente
    assert "git worktree remove" in ambiente

    events = state.read_events(task)
    created = [row for row in events if row.get("event") == "unit_created"]
    assert len(created) == 1
    assert created[0]["unit"] == "prova"
    assert created[0]["base"] == repo.head()
    assert created[0]["remotes"]["origin"], "i ref del remoto sono registrati alla creazione"


def test_the_push_from_the_unit_copy_fails_and_the_remote_does_not_move(task, repo, tmp_path):
    record = _create(task, repo, tmp_path)
    folder = record["worktree"]
    before = repo.remote_refs()
    git_in(folder, "commit", "-q", "--allow-empty", "-m", "lavoro")

    explicit = git_in(folder, "push", "origin", record["branch"], check=False)
    assert explicit.returncode != 0
    assert "nexgen-sup-disabled" in (explicit.stderr + explicit.stdout).lower()

    with_upstream = git_in(folder, "push", "-u", "origin", record["branch"], check=False)
    assert with_upstream.returncode != 0

    assert repo.remote_refs() == before, "il remoto non deve essersi mosso"


def test_the_block_lives_only_in_the_unit_copy(task, repo, tmp_path):
    record = _create(task, repo, tmp_path)
    assert git_in(repo.path, "config", "--get", "remote.origin.pushurl", check=False).returncode == 1
    assert git_in(record["worktree"], "config", "--get", "remote.origin.pushurl").stdout.strip() == worktree.BLOCKED_PUSH_URL


def test_the_shared_hooks_are_left_alone(task, repo, tmp_path):
    hook = repo.path / ".git" / "hooks" / "pre-push"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

    _create(task, repo, tmp_path)

    assert hook.read_text(encoding="utf-8") == "#!/bin/sh\nexit 0\n"


def test_creating_the_same_unit_twice_is_refused(task, repo, tmp_path, lane):
    _create(task, repo, tmp_path)
    with pytest.raises(worktree.WorktreeError, match="esiste già"):
        _create(task, repo, tmp_path)


def test_an_unknown_base_is_refused(task, repo, tmp_path):
    with pytest.raises(worktree.WorktreeError, match="base sconosciuta"):
        _create(task, repo, tmp_path, base="non-esiste")


def test_a_checkout_that_is_not_a_repository_is_refused(task, tmp_path):
    elsewhere = tmp_path / "vuota"
    elsewhere.mkdir()
    with pytest.raises(worktree.WorktreeError, match="non è un repository"):
        worktree.create(
            task, "prova", repo=elsewhere, base="main", brief=_brief(tmp_path), allow=["docs/*.md"],
        )


def test_an_empty_boundary_is_refused(task, repo, tmp_path):
    with pytest.raises(worktree.WorktreeError, match="confine vuoto"):
        _create(task, repo, tmp_path, allow=[])


def test_a_missing_brief_is_refused(task, repo, tmp_path):
    with pytest.raises(worktree.WorktreeError, match="brief inesistente"):
        _create(task, repo, tmp_path, brief=str(tmp_path / "manca.md"))


def test_an_unknown_task_is_refused(repo, tmp_path, lane):
    with pytest.raises(state.SupervisionError, match="compito inesistente"):
        worktree.create(
            "20260101-000000-mai-creato", "prova",
            repo=repo.path, base="main", brief=_brief(tmp_path), allow=["*"],
        )


def test_the_created_unit_shows_up_in_the_status(task, repo, tmp_path):
    record = _create(task, repo, tmp_path)
    status = state.status(task)
    units = status["units"]
    assert [unit["unit"] for unit in units] == ["prova"]
    assert units[0]["state"] == "creata"
    assert units[0]["branch"] == record["branch"]
    assert units[0]["cost_usd"] is None
    assert json.loads(json.dumps(status["units"]))[0]["worktree"] == record["worktree"]
