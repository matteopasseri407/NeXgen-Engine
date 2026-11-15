"""`.worktreeinclude`: la copia porta i file ignorati che il lavoro richiede, e solo quelli.

Il file è una convenzione che altri strumenti leggevano già: un elenco in sintassi
gitignore di file ignorati da git che ogni nuova copia deve avere. Le due regole si
provano separate, perché sbagliarne una è il modo di copiare un file tracciato
sopra sé stesso: un file si copia solo quando un pattern lo nomina **e** git lo
ignora davvero.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from nexgen_local import supervision_state as state
from nexgen_local import supervision_worktree as worktree

from supervision_repo import build

VERIFY_OK = f'"{sys.executable}" -c "print(1)"'


@pytest.fixture
def lane(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "lane_state_dir", lambda: tmp_path / "lane")
    return tmp_path / "lane" / "supervision"


@pytest.fixture
def repo(tmp_path):
    return build(tmp_path / "sandbox")


@pytest.fixture
def task(lane):
    return state.start("prova include")["id"]


def _brief(tmp_path: Path) -> Path:
    brief = tmp_path / "brief.md"
    brief.write_text("# Brief\n\nSolo fatti.\n", encoding="utf-8")
    return brief


def _create(task, repo, tmp_path, unit: str = "prova") -> dict:
    return worktree.create(
        task, unit, repo=repo.path, base="main", brief=str(_brief(tmp_path)),
        allow=["docs/*.md"], verify=VERIFY_OK,
    )


def _ignoring_repo(repo, *, patterns: str | None = None) -> None:
    """A repository that ignores a `.env`, logs and `node_modules`, and says what to carry."""
    repo.write(".gitignore", ".env\n*.log\nnode_modules/\n")
    if patterns is not None:
        repo.write(worktree.INCLUDE_FILE, patterns)
    repo.commit("ignore")


def test_a_file_is_carried_only_when_a_pattern_names_it_and_git_ignores_it(task, repo, tmp_path):
    _ignoring_repo(repo, patterns="# roba che serve al lavoro\n\n.env\nlogs/*.log\nscratch.txt\n")
    (repo.path / ".env").write_text("SEGRETO=1\n", encoding="utf-8")
    (repo.path / "logs").mkdir()
    (repo.path / "logs" / "app.log").write_text("riga\n", encoding="utf-8")
    (repo.path / "node_modules").mkdir()
    (repo.path / "node_modules" / "a.js").write_text("x\n", encoding="utf-8")
    (repo.path / "scratch.txt").write_text("non ignorato\n", encoding="utf-8")

    record = _create(task, repo, tmp_path, "prova include")
    folder = Path(record["worktree"])

    assert record["included"] == [".env", "logs/app.log"]
    assert (folder / ".env").read_text(encoding="utf-8") == "SEGRETO=1\n"
    assert (folder / "logs" / "app.log").read_text(encoding="utf-8") == "riga\n"
    assert not (folder / "node_modules" / "a.js").exists(), "ignorato da git ma non nominato dal file"
    assert not (folder / "scratch.txt").exists(), "nominato dal file ma non ignorato da git"


def test_the_environment_says_which_files_were_carried(task, repo, tmp_path):
    _ignoring_repo(repo, patterns=".env\n")
    (repo.path / ".env").write_text("A=1\n", encoding="utf-8")

    _create(task, repo, tmp_path, "ambiente")

    environment = (state.open_task(task) / "22-ambiente-ambiente.md").read_text(encoding="utf-8")
    assert "- file inclusi da `.worktreeinclude`: .env" in environment


def test_a_repository_without_the_file_carries_nothing(task, repo, tmp_path):
    _ignoring_repo(repo)
    (repo.path / ".env").write_text("A=1\n", encoding="utf-8")

    record = _create(task, repo, tmp_path, "senza")

    assert record["included"] == []
    assert not (Path(record["worktree"]) / ".env").exists()


def test_comments_and_blank_lines_are_not_patterns(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / worktree.INCLUDE_FILE).write_text("# commento\n\n.env\n  \n*.log\n", encoding="utf-8")

    assert worktree.include_patterns(root) == [".env", "*.log"]
    assert worktree.include_patterns(tmp_path) == [], "nessun file: nessun pattern, e nessun errore"
