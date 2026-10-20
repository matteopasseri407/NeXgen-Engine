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


def test_a_renamed_version_file_is_refused_as_a_bump(task, repo, tmp_path):
    """Una rinomina non deve nascondere il file di release al controllo del bump."""
    repo.write("VERSION", "2.16.7\n")
    repo.commit("chore: la base ha un VERSION")
    repo.git("push", "-q", "origin", "main")
    folder = _unit(task, repo, tmp_path, allow=["**"])
    git_in(folder, "mv", "VERSION", "versione-nascosta.txt")
    git_in(folder, "commit", "-q", "-m", "chore: sposta la versione")
    assert gates.collect(task, "prova")["verdict"] == "PASS"
    review.approve(task, "prova", "il confine è tutto")
    before = _main_of(repo.path)

    with pytest.raises(publish_module.PublishError, match="bump di versione") as caught:
        publish_module.publish(task, "prova", repo=repo.path, branch="main")

    assert "VERSION" in str(caught.value)
    assert _main_of(repo.path) == before


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


@pytest.mark.parametrize("command", [
    "git push origin +developer",
    "git push origin :developer",
    "git push --force-with-lease=developer origin main",
    "git push -vf origin",
])
def test_force_by_another_spelling_is_refused_before_it_runs(command):
    """`+refspec`, `:refspec`, `--force-with-lease=<ref>` and bundled short flags are all force."""
    with pytest.raises(publish_module.PublishError, match="force-push: il motore non lo fa mai"):
        publish_module._refuse_force(command)


def test_a_default_push_does_not_follow_tags(task, repo, tmp_path):
    """`push.followTags` would send annotated tags with a plain branch push: the engine says no."""
    _approved_unit(task, repo, tmp_path)
    git_in(repo.path, "tag", "-a", "v-base", "-m", "un tag locale")
    git_in(repo.path, "config", "push.followTags", "true")

    result = publish_module.publish(
        task, "prova", repo=repo.path, branch="main", remote="origin", push=True,
    )

    assert result["pushed"] is True
    remote_tags = git_in(tmp_path, "ls-remote", "--tags", str(repo.bare)).stdout
    assert "refs/tags/v-base" not in remote_tags, "il tag resta locale"


def _declared_script(tmp_path, name: str, body: str, *args: str) -> str:
    """A declared push command whose force or tag push is hidden inside a script, not in its tokens."""
    script = tmp_path / name
    script.write_text(body, encoding="utf-8")
    quoted = [f'"{sys.executable}"', f'"{script}"', *[f'"{arg}"' for arg in args]]
    return " ".join(quoted)


def test_a_declared_push_that_forces_history_is_caught_after_it_ran(task, repo, tmp_path):
    _approved_unit(task, repo, tmp_path)
    command = _declared_script(tmp_path, "forza.py", (
        "import subprocess, sys\n"
        "repo = sys.argv[1]\n"
        "done = subprocess.run(['git', '-C', repo, 'push', '--force', 'origin', 'HEAD~1:main'], "
        "capture_output=True, text=True)\n"
        "sys.exit(done.returncode)\n"
    ), str(repo.path))

    with pytest.raises(publish_module.PublishError, match="ha forzato la storia") as caught:
        publish_module.publish(task, "prova", repo=repo.path, branch="main", push_with=command)

    assert "main" in str(caught.value)
    assert _remote_main(repo) != _main_of(repo.path), "la forzatura è arrivata al remoto e il motore l'ha vista"
    event = [row for row in state.read_events(task) if row.get("event") == "unit_published"][-1]
    assert event["pushed"] is False
    assert "forzato" in event["output"]


def test_a_declared_push_that_pushes_a_tag_is_caught_after_it_ran(task, repo, tmp_path):
    _approved_unit(task, repo, tmp_path)
    command = _declared_script(tmp_path, "tag.py", (
        "import subprocess, sys\n"
        "repo = sys.argv[1]\n"
        "for args in (['push', 'origin', 'main'], ['tag', 'v-fuga'], ['push', 'origin', 'v-fuga']):\n"
        "    done = subprocess.run(['git', '-C', repo, *args], capture_output=True, text=True)\n"
        "    if done.returncode:\n"
        "        sys.exit(done.returncode)\n"
    ), str(repo.path))

    with pytest.raises(publish_module.PublishError, match="creato o spinto tag") as caught:
        publish_module.publish(task, "prova", repo=repo.path, branch="main", push_with=command)

    assert "v-fuga" in str(caught.value)
    remote_tags = git_in(tmp_path, "ls-remote", "--tags", str(repo.bare)).stdout
    assert "refs/tags/v-fuga" in remote_tags


def test_a_push_with_command_that_cannot_start_still_leaves_the_publication_on_file(task, repo, tmp_path):
    """I commit sono sul ramo anche quando la spinta non parte: il diario deve dirlo, e si spinge dopo."""
    _approved_unit(task, repo, tmp_path)
    base = _main_of(repo.path)

    with pytest.raises(publish_module.PublishError, match="comando non avviato"):
        publish_module.publish(
            task, "prova", repo=repo.path, branch="main", push_with="manca-questo-comando",
        )

    event = [row for row in state.read_events(task) if row.get("event") == "unit_published"][-1]
    assert event["pushed"] is False
    assert "comando non avviato" in event["output"]

    retry = publish_module.publish(task, "prova", repo=repo.path, branch="main", push=True)

    assert retry["replayed"] is False, "la pubblicazione locale si spinge, non si rifà"
    assert retry["pushed"] is True
    assert _remote_main(repo) == _main_of(repo.path)
    assert git_in(repo.path, "rev-list", "--count", f"{base}..HEAD").stdout.strip() == "1"


def test_a_local_only_publication_is_pushed_without_replaying_it(task, repo, tmp_path):
    _approved_unit(task, repo, tmp_path)
    base = _main_of(repo.path)
    first = publish_module.publish(task, "prova", repo=repo.path, branch="main")
    local = _main_of(repo.path)

    second = publish_module.publish(task, "prova", repo=repo.path, branch="main", push=True)

    assert first["pushed"] is False
    assert second["replayed"] is False
    assert second["pushed"] is True
    assert _main_of(repo.path) == local, "il cherry-pick non si ripete"
    assert git_in(repo.path, "rev-list", "--count", f"{base}..HEAD").stdout.strip() == "1"
    assert _remote_main(repo) == local
    last = [row for row in state.read_events(task) if row.get("event") == "unit_published"][-1]
    assert last["pushed"] is True


def test_a_local_only_publication_pushes_the_published_head_not_a_later_commit(task, repo, tmp_path):
    """Il motore non pubblica ciò che non ha revisionato: il commit aggiunto dopo resta locale."""
    _approved_unit(task, repo, tmp_path)
    publish_module.publish(task, "prova", repo=repo.path, branch="main")
    published_head = _main_of(repo.path)
    added = _commit(repo.path, "docs: un commit del proprietario", {"docs/dopo.md": "dopo\n"})

    result = publish_module.publish(task, "prova", repo=repo.path, branch="main", push=True)

    assert result["pushed"] is True
    assert _remote_main(repo) == published_head, "il remoto riceve la head pubblicata, non quella corrente"
    assert _main_of(repo.path) == added, "il commit aggiunto resta sul ramo locale"
    assert "docs: un commit del proprietario" not in git_in(repo.bare, "log", "--format=%s", "main").stdout


def test_a_local_only_publication_without_push_is_refused_with_the_hint(task, repo, tmp_path):
    _approved_unit(task, repo, tmp_path)
    publish_module.publish(task, "prova", repo=repo.path, branch="main")
    head = _main_of(repo.path)

    with pytest.raises(publish_module.PublishError, match="non è stata spinta") as caught:
        publish_module.publish(task, "prova", repo=repo.path, branch="main")

    assert "--push" in str(caught.value)
    assert _main_of(repo.path) == head


def test_a_local_only_publication_whose_commits_left_the_branch_is_refused(task, repo, tmp_path):
    _approved_unit(task, repo, tmp_path)
    publish_module.publish(task, "prova", repo=repo.path, branch="main")
    git_in(repo.path, "reset", "--hard", "HEAD~1")

    with pytest.raises(publish_module.PublishError, match="non contiene più i commit pubblicati"):
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
    assert "spinta eseguita: git push --no-follow-tags origin main" in out
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
