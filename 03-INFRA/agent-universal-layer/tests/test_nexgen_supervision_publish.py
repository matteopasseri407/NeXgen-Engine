"""La pubblicazione: cherry-pick sul ramo del proprietario, in avanti, mai con forza.

Repository git veri, remoto `--bare` locale, nessuna firma GPG vera: quello che si
prova è l'ordine dei commit, il fatto che il remoto riceva esattamente la head
locale, e i rifiuti nominati — non approvata, checkout sporco, ramo sbagliato,
bump di VERSION, force-push, conflitto annullato.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from nexgen_local import cli
from nexgen_local import supervision_gates as gates
from nexgen_local import supervision_publish as publish_module
from nexgen_local import supervision_review as review
from nexgen_local import supervision_state as state
from nexgen_local import supervision_worktree as worktree

from supervision_repo import build, git_in

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
    return state.start("prova fase 3a")["id"]


def _unit(task, repo, tmp_path, *, allow=("docs/*.md",), verify=VERIFY_OK) -> Path:
    brief = tmp_path / "brief.md"
    brief.write_text("# Brief dell'unità prova\n\nSolo fatti.\n", encoding="utf-8")
    record = worktree.create(
        task, "prova", repo=repo.path, base="main", brief=str(brief),
        allow=list(allow), verify=verify,
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


def _approved_unit(task, repo, tmp_path, *, files: dict[str, str] | None = None, allow=("docs/*.md",)) -> Path:
    """A unit with one commit that passed the gates and was approved."""
    folder = _unit(task, repo, tmp_path, allow=allow)
    _commit(folder, "docs: una riga nuova", files or {"docs/guida.md": "# guida\n\nriga pubblicabile.\n"})
    assert gates.collect(task, "prova")["verdict"] == "PASS"
    review.approve(task, "prova", "una sola cosa, cancelli verdi")
    return folder


def _main_of(checkout: Path) -> str:
    return git_in(checkout, "rev-parse", "HEAD").stdout.strip()


def _remote_main(repo) -> str:
    return git_in(repo.bare, "rev-parse", "main").stdout.strip()


def _advance_remote(tmp_path, repo) -> Path:
    """Another actor pushes one commit to the bare remote's `main`."""
    elsewhere = tmp_path / "estraneo"
    git_in(tmp_path, "clone", "-q", "-b", "main", str(repo.bare), str(elsewhere))
    git_in(elsewhere, "config", "user.email", "e@example.com")
    git_in(elsewhere, "config", "user.name", "e")
    (elsewhere / "fuori.txt").write_text("fuori dal motore\n", encoding="utf-8")
    git_in(elsewhere, "add", "-A")
    git_in(elsewhere, "commit", "-q", "-m", "chore: fuori dal motore")
    git_in(elsewhere, "push", "-q", "origin", "main")
    return elsewhere


def test_publish_applies_the_commits_in_order_and_pushes_them_forward(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path)
    base = _main_of(repo.path)
    _commit(folder, "docs: prima", {"docs/prima.md": "prima\n"})
    _commit(folder, "docs: seconda", {"docs/seconda.md": "seconda\n"})
    assert gates.collect(task, "prova")["verdict"] == "PASS"
    review.approve(task, "prova", "due commit piccoli, cancelli verdi")

    result = publish_module.publish(
        task, "prova", repo=repo.path, branch="main", remote="origin", push=True,
    )

    assert [commit["subject"] for commit in result["commits"]] == ["docs: prima", "docs: seconda"]
    assert result["pushed"] is True
    published = git_in(repo.path, "log", "--format=%s", f"{base}..HEAD").stdout.split("\n")[:2]
    assert published == ["docs: seconda", "docs: prima"], "l'ordine dei commit si conserva"
    assert (repo.path / "docs" / "prima.md").is_file()
    assert (repo.path / "docs" / "seconda.md").is_file()
    assert _remote_main(repo) == _main_of(repo.path), "il remoto riceve esattamente la head locale"
    assert "commit.gpgsign = false" in result["signing"]
    assert "N" in result["signing"], "senza firma configurata i commit pubblicati sono non firmati"
    marks = git_in(repo.path, "log", "--format=%G?", f"{base}..HEAD").stdout.split()
    assert set(marks) == {"N"}, "la firma segue la configurazione, non viene inventata"
    published_event = [row for row in state.read_events(task) if row.get("event") == "unit_published"][-1]
    assert published_event["pushed"] is True
    assert published_event["commits"] == [item["published"] for item in result["commits"]]
    status = state.status(task)["units"][0]
    assert status["published"] == published_event["commits"]
    assert status["state"] == "pubblicata"


def test_publish_without_push_stops_after_the_local_cherry_pick(task, repo, tmp_path, capsys):
    _approved_unit(task, repo, tmp_path)
    before = _remote_main(repo)
    capsys.readouterr()

    assert cli.main([
        "sup", "publish", task, "prova", "--repo", str(repo.path), "--branch", "main",
    ]) == 0

    out = capsys.readouterr().out
    assert "nessuna spinta" in out
    assert "resta da fare: `git push origin main`" in out
    assert _remote_main(repo) == before, "senza --push il remoto non si muove"
    assert _main_of(repo.path) != before, "il cherry-pick locale è stato fatto"


def test_publish_is_refused_when_the_unit_is_not_approved(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path)
    _commit(folder, "docs: una riga", {"docs/guida.md": "# guida\n\nriga.\n"})
    gates.collect(task, "prova")

    with pytest.raises(publish_module.PublishError, match="non approvata"):
        publish_module.publish(task, "prova", repo=repo.path, branch="main")


def test_publish_is_refused_when_the_head_moved_after_the_approval(task, repo, tmp_path):
    folder = _approved_unit(task, repo, tmp_path)
    _commit(folder, "docs: un commit dopo l'approvazione", {"docs/tardi.md": "tardi\n"})

    with pytest.raises(publish_module.PublishError, match="cambiata dopo l'approvazione"):
        publish_module.publish(task, "prova", repo=repo.path, branch="main")


def test_publish_is_refused_when_the_owner_checkout_is_dirty(task, repo, tmp_path):
    _approved_unit(task, repo, tmp_path)
    (repo.path / "appunti.txt").write_text("non committato\n", encoding="utf-8")

    with pytest.raises(publish_module.PublishError, match="checkout sporco"):
        publish_module.publish(task, "prova", repo=repo.path, branch="main")

    assert (repo.path / "appunti.txt").is_file(), "la copia del proprietario non si tocca"


def test_publish_is_refused_on_the_wrong_branch(task, repo, tmp_path):
    _approved_unit(task, repo, tmp_path)
    git_in(repo.path, "checkout", "-q", "-b", "altro")

    with pytest.raises(publish_module.PublishError, match="il checkout è su altro, non su main"):
        publish_module.publish(task, "prova", repo=repo.path, branch="main")


def test_publish_is_refused_when_the_checkout_is_the_unit_copy(task, repo, tmp_path):
    folder = _approved_unit(task, repo, tmp_path)

    with pytest.raises(publish_module.PublishError, match="copia dell'unità"):
        publish_module.publish(task, "prova", repo=folder, branch="main")


def test_a_version_bump_is_refused_by_name_even_inside_the_boundary(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path, allow=["**"])
    _commit(folder, "chore: alza la versione", {"VERSION": "2.99.0\n"})
    assert gates.collect(task, "prova")["verdict"] == "PASS", "il confine lo permetteva"
    review.approve(task, "prova", "il confine lo dichiarava")
    before = _main_of(repo.path)

    with pytest.raises(publish_module.PublishError, match="bump di versione") as caught:
        publish_module.publish(task, "prova", repo=repo.path, branch="main")

    assert "VERSION" in str(caught.value)
    assert "nexgen release ship" in str(caught.value)
    assert _main_of(repo.path) == before, "niente è stato applicato"


def test_a_remote_that_moved_is_refused_with_the_name_force_push(task, repo, tmp_path):
    _approved_unit(task, repo, tmp_path)
    _advance_remote(tmp_path, repo)
    before = _main_of(repo.path)

    with pytest.raises(publish_module.PublishError, match="servirebbe un force-push: il motore non lo fa mai") as caught:
        publish_module.publish(task, "prova", repo=repo.path, branch="main", push=True)

    assert "non è antenato" in str(caught.value)
    assert _main_of(repo.path) == before, "il checkout non è stato toccato"


def test_a_cherry_pick_conflict_aborts_and_leaves_the_checkout_where_it_was(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path)
    _commit(folder, "docs: la riga dell'unità", {"docs/guida.md": "# guida\n\nriga dell'unità.\n"})
    owner = _commit(repo.path, "docs: la riga del proprietario", {"docs/guida.md": "# guida\n\nriga del proprietario.\n"})
    assert gates.collect(task, "prova")["verdict"] == "PASS"
    review.approve(task, "prova", "verde prima del conflitto")
    remote_before = _remote_main(repo)

    with pytest.raises(publish_module.PublishError, match="conflitto") as caught:
        publish_module.publish(task, "prova", repo=repo.path, branch="main", push=True)

    assert "docs/guida.md" in str(caught.value)
    assert _main_of(repo.path) == owner, "il checkout è tornato al commit che c'era prima"
    assert git_in(repo.path, "status", "--porcelain").stdout.strip() == "", "nessun residuo del conflitto"
    assert git_in(repo.path, "rev-parse", "--verify", "CHERRY_PICK_HEAD", check=False).returncode != 0
    assert _remote_main(repo) == remote_before, "niente è stato pubblicato"


def test_a_push_with_command_that_fails_is_declared_not_done(task, repo, tmp_path, capsys):
    _approved_unit(task, repo, tmp_path)
    before = _remote_main(repo)
    capsys.readouterr()

    assert cli.main([
        "sup", "publish", task, "prova", "--repo", str(repo.path), "--branch", "main",
        "--push-with", f'"{sys.executable}" -c "import sys; sys.exit(4)"',
    ]) == 1

    out = capsys.readouterr().out
    assert "pubblicazione dichiarata non riuscita" in out
    assert _remote_main(repo) == before
    event = [row for row in state.read_events(task) if row.get("event") == "unit_published"][-1]
    assert event["pushed"] is False
    assert state.status(task)["units"][0]["state"] == "pubblicata in locale"


def test_a_push_with_command_that_pushes_is_the_one_that_decides(task, repo, tmp_path):
    _approved_unit(task, repo, tmp_path)

    result = publish_module.publish(
        task, "prova", repo=repo.path, branch="main",
        push_with=f'git -C "{repo.path}" push origin main',
    )

    assert result["pushed"] is True
    assert _remote_main(repo) == _main_of(repo.path)


def test_a_push_with_command_that_would_force_is_refused_before_it_runs(task, repo, tmp_path):
    _approved_unit(task, repo, tmp_path)
    marker = tmp_path / "eseguito.txt"

    with pytest.raises(publish_module.PublishError, match="force-push: il motore non lo fa mai"):
        publish_module.publish(
            task, "prova", repo=repo.path, branch="main",
            push_with=f'"cmd.exe" /c echo eseguito > "{marker}" & git push --force origin main',
        )

    assert not marker.exists(), "il comando non è stato eseguito"


def test_a_push_with_command_that_cannot_start_still_leaves_the_publication_on_file(task, repo, tmp_path):
    """I commit sono sul ramo anche quando la spinta non parte: il diario deve dirlo."""
    _approved_unit(task, repo, tmp_path)

    with pytest.raises(publish_module.PublishError, match="comando non avviato"):
        publish_module.publish(
            task, "prova", repo=repo.path, branch="main", push_with="manca-questo-comando",
        )

    event = [row for row in state.read_events(task) if row.get("event") == "unit_published"][-1]
    assert event["pushed"] is False
    assert "comando non avviato" in event["output"]
    with pytest.raises(publish_module.PublishError, match="già pubblicata"):
        publish_module.publish(task, "prova", repo=repo.path, branch="main", push=True)


def test_publishing_the_same_unit_twice_is_refused(task, repo, tmp_path):
    _approved_unit(task, repo, tmp_path)
    publish_module.publish(task, "prova", repo=repo.path, branch="main", push=True)
    head = _main_of(repo.path)

    with pytest.raises(publish_module.PublishError, match="già pubblicata"):
        publish_module.publish(task, "prova", repo=repo.path, branch="main", push=True)

    assert _main_of(repo.path) == head


def test_the_cli_prints_the_published_hashes_and_the_signature(task, repo, tmp_path, capsys):
    _approved_unit(task, repo, tmp_path)
    capsys.readouterr()

    assert cli.main([
        "sup", "publish", task, "prova", "--repo", str(repo.path), "--branch", "main", "--push",
    ]) == 0

    out = capsys.readouterr().out
    assert "unità prova · 1 commit su main" in out
    assert "docs: una riga nuova" in out
    assert "spinta eseguita: git push origin main" in out
    assert "commit.gpgsign = false" in out


def test_the_cli_prints_the_named_refusal(task, repo, tmp_path, capsys):
    folder = _unit(task, repo, tmp_path, allow=["**"])
    _commit(folder, "chore: alza la versione", {"VERSION": "2.99.0\n"})
    gates.collect(task, "prova")
    review.approve(task, "prova", "confine dichiarato")
    capsys.readouterr()

    assert cli.main([
        "sup", "publish", task, "prova", "--repo", str(repo.path), "--branch", "main",
    ]) == 1

    assert "bump di versione" in capsys.readouterr().err


def test_status_shows_the_round_the_approval_and_the_published_hash(task, repo, tmp_path, capsys):
    _approved_unit(task, repo, tmp_path)
    publish_module.publish(task, "prova", repo=repo.path, branch="main", push=True)
    capsys.readouterr()

    assert cli.main(["sup", "status", task]) == 0

    out = capsys.readouterr().out
    assert "giro 1 · approvata" in out
    assert "pubblicata" in out and "su main (spinta)" in out


def test_the_output_prints_on_a_windows_console_codepage(task, repo, tmp_path, capsys):
    """Una console Windows in cp1252 non deve far cadere il comando su un carattere.

    Il motore scrive su stdout anche quando è rediretto, e lì la tabella è cp1252:
    una freccia tipografica l'ha fatto cadere dopo aver già pubblicato, quindi il
    comando risultava fallito con i commit sul ramo. Un carattere in meno è il fix.
    """
    _approved_unit(task, repo, tmp_path)
    capsys.readouterr()

    assert cli.main([
        "sup", "publish", task, "prova", "--repo", str(repo.path), "--branch", "main", "--push",
    ]) == 0
    publish_out = capsys.readouterr().out
    publish_out.encode("cp1252")

    assert cli.main(["sup", "status", task]) == 0
    capsys.readouterr().out.encode("cp1252")
