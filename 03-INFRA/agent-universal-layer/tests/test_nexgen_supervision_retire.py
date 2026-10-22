"""Il ritiro di un'unità: copia e ramo se ne vanno solo quando i commit sono arrivati.

Repository git veri e nessun servizio vero: l'adattatore che libera la cartella è
sostituito, perché qui si prova la regola del proprietario ("a fine lavoro non
resta niente"), non il servizio condiviso. La cartella del compito resta sempre:
è il registro, non il banco di lavoro.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from nexgen_local import cli
from nexgen_local import supervision_adapters as adapters
from nexgen_local import supervision_cleanup as cleanup
from nexgen_local import supervision_publish as publish_module
from nexgen_local import supervision_retire as retire
from nexgen_local import supervision_review as review
from nexgen_local import supervision_state as state
from nexgen_local import supervision_worktree as worktree

from supervision_repo import build, git_in

VERIFY_OK = f'"{sys.executable}" -c "print(1)"'


@pytest.fixture(autouse=True)
def no_server(monkeypatch):
    """Nessun test parla al servizio condiviso: la liberazione della cartella è finta."""
    monkeypatch.setattr(
        adapters.get("opencode"), "dispose_location",
        lambda folder: (True, f"cartella liberata: {folder}"),
    )


@pytest.fixture
def lane(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "lane_state_dir", lambda: tmp_path / "lane")
    return tmp_path / "lane" / "supervision"


@pytest.fixture
def repo(tmp_path):
    return build(tmp_path / "sandbox")


@pytest.fixture
def task(lane):
    return state.start("prova ritiro")["id"]


def _prepared(task: str, repo, tmp_path: Path, *, published: bool = False) -> tuple[Path, str]:
    """A unit with one commit, a PASS collection and an approval; optionally already published."""
    brief = tmp_path / "brief.md"
    brief.write_text("# Brief dell'unità prova\n", encoding="utf-8")
    record = worktree.create(
        task, "prova", repo=repo.path, base="main", brief=str(brief),
        allow=["docs/*.md"], verify=VERIFY_OK,
    )
    folder = Path(record["worktree"])
    (folder / "docs" / "guida.md").write_text("# guida\n\nriga nuova.\n", encoding="utf-8")
    git_in(folder, "add", "-A")
    git_in(folder, "commit", "-q", "-m", "docs: una riga nuova")
    sha = git_in(folder, "rev-parse", "HEAD").stdout.strip()
    state.append_event(task, "unit_collected", unit="prova", verdict="PASS", head=sha)
    state.append_event(task, "unit_reviewed", unit="prova", round=1, verdict=state.APPROVED, head=sha)
    if published:
        git_in(repo.path, "checkout", "-q", "main")
        git_in(repo.path, "merge", "-q", "--ff-only", record["branch"])
        state.append_event(
            task, "unit_published", unit="prova", commits=[sha], head=sha,
            branch="main", repo=str(repo.path), pushed=False,
        )
    return folder, sha


def _branch_exists(repo, branch: str) -> bool:
    return bool(git_in(repo.path, "rev-parse", "--verify", f"refs/heads/{branch}", check=False).stdout.strip())


def test_publish_retires_the_copy_and_the_branch_once_the_commits_arrived(task, repo, tmp_path):
    folder, _ = _prepared(task, repo, tmp_path)

    result = publish_module.publish(
        task, "prova", repo=repo.path, branch="main", remote="origin", push=False,
    )

    retirement = result["retirement"]
    assert retirement["retired"] is True
    assert not folder.exists(), "la copia dell'unità non resta sul disco"
    assert not _branch_exists(repo, retirement["branch"]), "il ramo sup/* non resta"
    record = state.unit_record(task, "prova")
    assert record["published"] == [item["published"] for item in result["commits"]]
    assert record["published_head"] == result["commits"][-1]["source"]
    assert record["retired"] is True
    assert record["state"] == "pubblicata in locale", "pubblicata resta pubblicata, il ritiro è pulizia"
    assert state.open_task(task).is_dir(), "la cartella del compito resta come registro"


def test_an_approved_unit_that_is_not_published_keeps_its_head_in_an_archive_ref(task, repo, tmp_path):
    folder, sha = _prepared(task, repo, tmp_path)

    result = retire.retire(task, "prova", why="chiusa senza pubblicazione", archive=True)

    archived = git_in(repo.path, "rev-parse", f"refs/nexgen/archive/{task}/prova").stdout.strip()
    assert archived == sha, "la head sopravvive al ramo"
    assert result["archive"] == f"refs/nexgen/archive/{task}/prova"
    assert not folder.exists()
    assert not _branch_exists(repo, result["branch"])
    record = state.unit_record(task, "prova")
    assert record["retired"] is True and record["state"] == "ritirata"


def test_retire_refuses_when_nothing_says_the_commits_arrived(task, repo, tmp_path):
    folder, _ = _prepared(task, repo, tmp_path)

    with pytest.raises(retire.RetireError, match="non pubblicata"):
        retire.retire(task, "prova", why="pulizia")

    assert folder.is_dir(), "niente è stato cancellato"
    assert _branch_exists(repo, state.unit_record(task, "prova")["branch"])
    assert "unit_retired" not in [row["event"] for row in state.read_events(task)]


def test_retire_refuses_published_commits_without_a_publication_row(task, repo, tmp_path):
    folder, _ = _prepared(task, repo, tmp_path)

    with pytest.raises(retire.RetireError, match="il diario non dice"):
        retire.retire(task, "prova", why="pulizia", commits=["f" * 40])

    assert folder.is_dir(), "una verifica fallita non tocca la copia"
    assert _branch_exists(repo, state.unit_record(task, "prova")["branch"])


def test_retire_refuses_a_commit_that_exists_but_never_reached_the_published_branch(
    task, repo, tmp_path,
):
    folder, sha = _prepared(task, repo, tmp_path)
    # The commit exists in the repository, on the unit's own branch, but `main`
    # never received it: the old check only asked whether the object was there.
    state.append_event(
        task, "unit_published", unit="prova", commits=[sha], head=sha,
        branch="main", repo=str(repo.path), pushed=False,
    )

    with pytest.raises(retire.RetireError, match="non sono sul ramo main"):
        retire.retire(task, "prova", why="pulizia", commits=[sha])

    assert folder.is_dir(), "la copia resta quando il commit non è arrivato"
    assert _branch_exists(repo, state.unit_record(task, "prova")["branch"])
    assert "unit_retired" not in [row["event"] for row in state.read_events(task)]


def test_a_failed_removal_does_not_undo_the_publication(task, repo, tmp_path, monkeypatch):
    _prepared(task, repo, tmp_path)

    def refuse(repo_path, copy, name):
        raise retire.RetireError(f"copia di {name} non rimossa: un processo la tiene aperta")

    monkeypatch.setattr(retire, "_remove_copy", refuse)

    result = publish_module.publish(
        task, "prova", repo=repo.path, branch="main", remote="origin", push=False,
    )

    assert result["commits"], "la pubblicazione è avvenuta lo stesso"
    assert result["retirement"]["retired"] is False
    assert "non rimossa" in result["retirement"]["error"]
    events = [row["event"] for row in state.read_events(task)]
    assert "unit_retire_failed" in events, "il ritiro fallito è nel diario, non nascosto"
    assert state.unit_record(task, "prova")["published"] == [
        item["published"] for item in result["commits"]
    ]


def test_cleanup_lists_a_published_unit_still_on_disk_and_closes_it(task, repo, tmp_path):
    folder, _ = _prepared(task, repo, tmp_path, published=True)

    leftovers = cleanup.leftovers()
    assert [row["unit"] for row in leftovers] == ["prova"]
    assert leftovers[0]["copy"] == str(folder)
    assert leftovers[0]["branch"] == state.unit_record(task, "prova")["branch"]
    printed = cleanup.leftover_lines({"leftovers": leftovers, "locations": []})
    assert "copie e rami di unità finite ancora sul disco: 1" in printed

    result = cleanup.close_leftovers(leftovers)

    assert result["problems"] == []
    assert not folder.exists()
    assert not _branch_exists(repo, leftovers[0]["branch"])
    assert cleanup.leftovers() == [], "una volta chiusa non è più un residuo"


def test_a_closed_unit_with_nothing_left_is_not_listed_again(task, repo, tmp_path):
    leftover, _ = _prepared(task, repo, tmp_path, published=True)
    retire.retire(task, "prova", why="pubblicata", commits=retire.verified_commits(task, "prova"))

    assert not leftover.exists()
    assert cleanup.leftovers() == []


def test_the_command_line_closes_an_approved_unit_with_its_archive(task, repo, tmp_path, capsys):
    folder, sha = _prepared(task, repo, tmp_path)

    code = cli.main([
        "sup", "review", task, "prova", "--approve", "applicata a mano", "--close",
    ])

    out = capsys.readouterr().out
    assert code == 0
    assert "unità chiusa senza pubblicazione" in out
    assert "archivio refs/nexgen/archive/" in out
    assert git_in(repo.path, "rev-parse", f"refs/nexgen/archive/{task}/prova").stdout.strip() == sha
    assert not folder.exists()


def test_close_is_refused_without_an_approval(task, repo, tmp_path, capsys):
    corrections = tmp_path / "correzioni.md"
    corrections.write_text("una correzione\n", encoding="utf-8")

    code = cli.main(["sup", "review", task, "prova", "--rework", str(corrections), "--close"])

    assert code == 1
    assert "`--close` vale solo con `--approve`" in capsys.readouterr().err


def test_close_refuses_a_unit_that_was_published(task, repo, tmp_path):
    _prepared(task, repo, tmp_path, published=True)

    with pytest.raises(review.ReviewError, match="già pubblicata"):
        review.close(task, "prova")
