"""Il riallineamento dell'unità: la base che avanza, i conflitti, l'impronta della configurazione.

Il repository è vero perché il riallineamento è un rebase vero: si prova che i
commit dell'unità finiscano sulla punta nuova, che un conflitto lasci la copia
com'era, e che il diario porti la base nuova — quella che `sup collect` legge per
sapere quali commit sono dell'unità.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from nexgen_local import cli
from nexgen_local import supervision_gitconfig as gitconfig
from nexgen_local import supervision_rebase as rebase
from nexgen_local import supervision_state as state
from nexgen_local import supervision_worktree as worktree

from supervision_repo import build, git_in


@pytest.fixture
def lane(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "lane_state_dir", lambda: tmp_path / "lane")
    return tmp_path / "lane"


@pytest.fixture
def repo(tmp_path):
    return build(tmp_path / "sandbox")


@pytest.fixture
def task(lane):
    return state.start("prova riallineamento")["id"]


@pytest.fixture
def folder(task, repo, tmp_path) -> Path:
    """L'unità già creata, come dopo `sup unit`."""
    brief = tmp_path / "brief.md"
    brief.write_text("# Brief dell'unità prova\n\nSolo fatti.\n", encoding="utf-8")
    record = worktree.create(
        task, "prova", repo=repo.path, base="main", brief=str(brief),
        allow=["**"], verify=f'"{sys.executable}" -c "print(1)"',
    )
    return Path(record["worktree"])


def _commit(folder: Path, message: str, files: dict[str, str]) -> str:
    for name, text in files.items():
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    git_in(folder, "add", "-A")
    git_in(folder, "commit", "-q", "-m", message)
    return git_in(folder, "rev-parse", "HEAD").stdout.strip()


def _advance_main(repo, message: str, files: dict[str, str]) -> str:
    for name, text in files.items():
        target = repo.path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return repo.commit(message)


def _events(task: str, name: str) -> list[dict]:
    return [row for row in state.read_events(task) if row.get("event") == name]


# --- la base che avanza -----------------------------------------------------


def test_the_rebase_moves_the_units_commits_onto_the_new_base(task, repo, folder):
    unit_head = _commit(folder, "docs: una riga dell'unità", {"docs/guida.md": "# guida\n\nriga unità.\n"})
    old_base = state.unit_record(task, "prova")["base"]
    new_base = _advance_main(repo, "docs: avanti", {"docs/nuovo.md": "# nuovo\n"})

    result = rebase.rebase(task, "prova")

    assert result["changed"] is True
    assert result["previous_base"] == old_base
    assert result["base"] == new_base
    assert result["head"] != unit_head
    assert git_in(folder, "rev-parse", "HEAD~1").stdout.strip() == new_base
    assert git_in(folder, "rev-list", "--count", f"{new_base}..HEAD").stdout.strip() == "1"
    assert (folder / "docs" / "nuovo.md").is_file()


def test_the_journal_carries_the_new_base_so_collect_looks_at_the_unit_only(task, repo, folder):
    _commit(folder, "docs: una riga dell'unità", {"docs/guida.md": "# guida\n\nriga unità.\n"})
    new_base = _advance_main(repo, "docs: avanti", {"docs/nuovo.md": "# nuovo\n"})

    rebase.rebase(task, "prova")

    record = state.unit_record(task, "prova")
    assert record["base"] == new_base
    assert record["state"] == "in corso"
    assert git_in(folder, "rev-list", "--count", f"{record['base']}..{record['head']}").stdout.strip() == "1"
    row = _events(task, "unit_rebased")[-1]
    assert row["base"] == new_base and row["previous_base"] != new_base


def test_a_branch_already_on_the_new_base_just_records_it(task, repo, folder):
    _commit(folder, "docs: una riga dell'unità", {"docs/guida.md": "# guida\n\nriga unità.\n"})
    new_base = _advance_main(repo, "docs: avanti", {"docs/nuovo.md": "# nuovo\n"})
    git_in(folder, "rebase", new_base)

    result = rebase.rebase(task, "prova")

    assert result["base"] == new_base
    assert git_in(folder, "rev-parse", "HEAD~1").stdout.strip() == new_base
    assert git_in(folder, "rev-list", "--count", f"{new_base}..HEAD").stdout.strip() == "1"


# --- i rifiuti --------------------------------------------------------------


def test_a_conflict_stops_and_leaves_the_unit_exactly_as_it_was(task, repo, folder):
    before = _commit(folder, "docs: riga dell'unità", {"docs/guida.md": "# guida\n\ndall'unità.\n"})
    _advance_main(repo, "docs: riga del proprietario", {"docs/guida.md": "# guida\n\ndal proprietario.\n"})

    with pytest.raises(rebase.RebaseError) as exc:
        rebase.rebase(task, "prova")

    assert "conflitto" in str(exc.value)
    assert git_in(folder, "rev-parse", "HEAD").stdout.strip() == before
    assert git_in(folder, "status", "--porcelain").stdout.strip() == ""
    assert not _events(task, "unit_rebased")


def test_uncommitted_work_is_refused_before_anything_moves(task, repo, folder):
    before = git_in(folder, "rev-parse", "HEAD").stdout.strip()
    (folder / "docs" / "guida.md").write_text("# guida\n\nnon committata.\n", encoding="utf-8")
    _advance_main(repo, "docs: avanti", {"docs/nuovo.md": "# nuovo\n"})

    with pytest.raises(rebase.RebaseError) as exc:
        rebase.rebase(task, "prova")

    assert "non committate" in str(exc.value)
    assert git_in(folder, "rev-parse", "HEAD").stdout.strip() == before
    assert not _events(task, "unit_rebased")


def test_an_untracked_file_does_not_block_the_rebase(task, repo, folder):
    """Un artefatto non tracciato non è lavoro da committare: git rebase lo lascia dov'è."""
    _commit(folder, "docs: una riga dell'unità", {"docs/guida.md": "# guida\n\nriga unità.\n"})
    new_base = _advance_main(repo, "docs: avanti", {"docs/nuovo.md": "# nuovo\n"})
    (folder / "appunto.txt").write_text("non tracciato\n", encoding="utf-8")

    result = rebase.rebase(task, "prova")

    assert result["changed"] is True
    assert result["base"] == new_base
    assert (folder / "appunto.txt").is_file(), "il file non tracciato resta dov'era"


def test_a_rewritten_base_is_refused_instead_of_replayed(task, repo, folder):
    _commit(folder, "docs: una riga dell'unità", {"docs/guida.md": "# guida\n\nriga unità.\n"})
    git_in(repo.path, "checkout", "-q", "--orphan", "riscritta")
    repo.write("docs/altro.md", "# altro\n")
    repo.commit("docs: storia riscritta")
    git_in(repo.path, "branch", "-M", "main")

    with pytest.raises(rebase.RebaseError) as exc:
        rebase.rebase(task, "prova")

    assert "riscritta" in str(exc.value)


def test_a_published_unit_is_never_rewritten(task, folder):
    state.append_event(task, "unit_published", unit="prova", commits=[], head="a" * 40, branch="main")

    with pytest.raises(rebase.RebaseError) as exc:
        rebase.rebase(task, "prova")

    assert "pubblicata" in str(exc.value)


# --- la configurazione condivisa -------------------------------------------


def test_a_base_that_did_not_move_and_a_configuration_that_did_not_change_is_a_no_op(task, folder):
    result = rebase.rebase(task, "prova")

    assert result["changed"] is False
    assert "niente da riallineare" in result["reason"]
    assert not _events(task, "unit_rebased")


def test_a_deliberate_config_change_is_absorbed_by_the_command(task, folder):
    git_in(folder, "config", "--add", "nexgen.prova", "1")
    record = state.unit_record(task, "prova")
    assert gitconfig.check(folder, record)[0] is False

    result = rebase.rebase(task, "prova")

    assert result["changed"] is True
    assert gitconfig.check(folder, state.unit_record(task, "prova"))[0] is True


# --- il verbo ---------------------------------------------------------------


def test_the_verb_prints_the_two_bases_and_the_new_head(task, repo, folder, capsys):
    _commit(folder, "docs: una riga dell'unità", {"docs/guida.md": "# guida\n\nriga unità.\n"})
    new_base = _advance_main(repo, "docs: avanti", {"docs/nuovo.md": "# nuovo\n"})

    assert cli.main(["sup", "rebase", task, "prova"]) == 0

    out = capsys.readouterr().out
    assert new_base[:12] in out
    assert "impronta rifatta" in out
    assert "sup collect" in out


def test_the_verb_exits_non_zero_on_a_conflict(task, repo, folder, capsys):
    _commit(folder, "docs: riga dell'unità", {"docs/guida.md": "# guida\n\ndall'unità.\n"})
    _advance_main(repo, "docs: riga del proprietario", {"docs/guida.md": "# guida\n\ndal proprietario.\n"})

    assert cli.main(["sup", "rebase", task, "prova"]) == 1

    assert "conflitto" in capsys.readouterr().err
