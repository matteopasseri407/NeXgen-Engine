"""Il giro del ricognitore: una copia staccata, un brief in un file, la copia ricontrollata.

Il repository è vero, perché quello che si prova è il confine: la copia non deve
appartenere a nessun ramo e deve tornare esattamente com'era. L'esploratore è
finto e le impostazioni di fiducia vivono in una cartella temporanea: nessun test
apre una scheda vera né tocca le impostazioni dell'esploratore installato.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from nexgen_local import cli
from nexgen_local import supervision_scout as scout
from nexgen_local import supervision_state as state
from nexgen_local import supervision_trust as trust

from supervision_repo import build, git_in


@pytest.fixture
def lane(tmp_path, monkeypatch):
    """La lane in una cartella temporanea: nessun test tocca lo stato vero."""
    monkeypatch.setattr(state, "lane_state_dir", lambda: tmp_path / "lane")
    return tmp_path / "lane"


@pytest.fixture(autouse=True)
def fake_home(tmp_path, monkeypatch):
    """Le impostazioni dell'esploratore restano finte in ogni test di questo file."""
    settings = tmp_path / "home" / trust.SETTINGS_RELATIVE
    monkeypatch.setattr(trust, "settings_path", lambda home=None: settings)
    return settings


@pytest.fixture
def repo(tmp_path):
    return build(tmp_path)


@pytest.fixture
def task(lane, repo):
    """Un compito con un'unità già creata: la ricognizione parte dal suo repository."""
    task_id = state.start("prova ricognizione")["id"]
    state.append_event(
        task_id, "unit_created",
        unit="prova", repo=str(repo.path), base=repo.head(), branch="sup/prova",
        worktree=str(repo.path), allow=["**"], remotes={},
    )
    return task_id


class Opener:
    """La scheda finta: prende il comando e la cartella e non avvia niente."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, title: str, argv, *, cwd, marker="") -> dict:
        self.calls.append({"title": title, "argv": list(argv), "cwd": Path(cwd), "marker": marker})
        return {"title": title, "launcher": "finto", "pid": 0, "cwd": str(cwd)}

    @property
    def last(self) -> dict:
        return self.calls[-1]


def _opened(task: str, question: str = "dove sta il confine?", **kwargs) -> dict:
    return scout.open_round(task, question, opener=kwargs.pop("opener", Opener()), **kwargs)


# --- la copia e il brief ----------------------------------------------------


def test_the_round_makes_a_fixed_detached_copy_and_writes_the_brief(task, repo):
    record = _opened(task)

    folder = state.open_task(task) / scout.FOLDER
    assert record["folder"] == str(folder)
    assert record["head"] == repo.head()
    assert git_in(folder, "rev-parse", "HEAD").stdout.strip() == repo.head()
    assert git_in(folder, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "HEAD"
    brief = Path(record["brief"])
    assert brief.is_file() and brief.name == scout.BRIEF
    text = brief.read_text(encoding="utf-8")
    assert "dove sta il confine?" in text
    assert record["report"] in text
    assert record["base"] in text


def test_the_brief_goes_to_the_explorer_as_a_pointer_not_as_the_whole_text(task, repo):
    opener = Opener()
    record = _opened(task, opener=opener)

    argv = opener.last["argv"]
    assert argv[0] == __import__("sys").executable
    assert "supervision_scout" in argv[-1] and record["task"] in argv[-1]
    assert opener.last["cwd"] == Path(record["folder"])
    assert "ricognitore" in opener.last["title"]


def test_the_next_round_uses_the_same_folder_and_loses_what_the_old_one_left(task, repo):
    first = _opened(task)
    leftover = Path(first["folder"]) / "appunti.txt"
    leftover.write_text("roba del giro prima\n", encoding="utf-8")

    second = _opened(task, "e adesso?")

    assert second["folder"] == first["folder"]
    assert not leftover.exists()
    assert second["round"] == first["round"] + 1
    assert second["report"] != first["report"]
    assert Path(second["report"]).name == scout.REPORT.format(round=second["round"])


def test_the_folder_is_trusted_once_and_stays_one_entry(task, fake_home):
    _opened(task)
    _opened(task, "secondo giro")

    listed = __import__("json").loads(fake_home.read_text(encoding="utf-8"))[trust.KEY]
    assert listed == [str((state.open_task(task) / scout.FOLDER).resolve())]


def test_every_round_is_journalled_with_its_own_number(task):
    _opened(task)
    _opened(task, "ancora")

    rows = [row for row in state.read_events(task) if row.get("event") == "scout_opened"]
    assert [row["round"] for row in rows] == [1, 2]
    assert all(row["folder"] == rows[0]["folder"] for row in rows)


# --- la verifica di fine giro ----------------------------------------------


def test_an_untouched_copy_comes_back_clean(task):
    _opened(task)

    found = scout.verdict(task)

    assert found["clean"] is True
    assert found["changes"] == []
    assert found["report_written"] is False


def test_an_untracked_file_is_named_and_does_not_dirty_the_copy(task):
    """Una cache lasciata da uno strumento non è una modifica al codice, ma si dice."""
    record = _opened(task)
    (Path(record["folder"]) / "cache.tmp").write_text("x\n", encoding="utf-8")

    found = scout.verdict(task)

    assert found["clean"] is True
    assert found["changes"] == []
    assert found["untracked"] == ["cache.tmp"]


def test_the_untracked_file_reaches_the_journal_and_the_line(task, monkeypatch, capsys):
    record = _opened(task)
    _install_explorer(monkeypatch, Path(record["report"]))
    (Path(record["folder"]) / "cache.tmp").write_text("x\n", encoding="utf-8")

    assert scout.run_in_tab(task) == 0

    done = [row for row in state.read_events(task) if row.get("event") == "scout_finished"][-1]
    assert done["clean"] is True
    assert done["untracked"] == ["cache.tmp"]
    assert "non tracciato, ignorato: cache.tmp" in capsys.readouterr().out


def test_a_changed_file_makes_the_copy_dirty(task):
    record = _opened(task)
    (Path(record["folder"]) / "README.md").write_text("# altro\n", encoding="utf-8")

    found = scout.verdict(task)

    assert found["clean"] is False
    assert any("README.md" in line for line in found["changes"])


def test_a_commit_made_in_the_copy_is_dirty_even_with_nothing_to_stage(task):
    record = _opened(task)
    folder = Path(record["folder"])
    git_in(folder, "config", "user.email", "t@example.com")
    git_in(folder, "config", "user.name", "t")
    git_in(folder, "config", "commit.gpgsign", "false")
    (folder / "nuovo.txt").write_text("x\n", encoding="utf-8")
    git_in(folder, "add", "-A")
    git_in(folder, "commit", "-q", "-m", "lo scout non doveva")

    found = scout.verdict(task)

    assert found["clean"] is False
    assert any(line.startswith("HEAD ") for line in found["changes"])


def test_a_written_report_is_read_and_counted(task):
    record = _opened(task)
    Path(record["report"]).write_text("# rapporto\n\nil confine sta qui.\n", encoding="utf-8")

    found = scout.verdict(task)

    assert found["report_written"] is True
    assert found["clean"] is True


def test_an_empty_report_is_not_a_report(task):
    record = _opened(task)
    Path(record["report"]).write_text("   \n", encoding="utf-8")

    assert scout.verdict(task)["report_written"] is False


# --- i rifiuti prima di aprire qualsiasi cosa -------------------------------


def test_a_task_without_a_unit_has_no_repository_to_copy(lane):
    task_id = state.start("compito senza unità")["id"]

    with pytest.raises(scout.ScoutError):
        _opened(task_id)

    assert not (state.open_task(task_id) / scout.FOLDER).exists()


def test_an_empty_question_is_refused_before_the_copy_is_made(task):
    with pytest.raises(scout.ScoutError):
        _opened(task, "   ")

    assert not (state.open_task(task) / scout.FOLDER).exists()


def test_a_round_that_was_never_opened_cannot_be_run_in_a_tab(task):
    with pytest.raises(scout.ScoutError):
        scout.run_in_tab(task)


# --- la scheda --------------------------------------------------------------


class Explorer:
    """`agy` finto: prende il comando, scrive il rapporto e non tocca il codice."""

    def __init__(self, report: Path, text: str = "# rapporto\n\nletto.\n") -> None:
        self.report = report
        self.text = text
        self.commands: list[list[str]] = []

    def __call__(self, command, *, cwd, check, timeout) -> None:
        self.commands.append(list(command))
        self.report.write_text(self.text, encoding="utf-8")


class FakeSubprocess:
    """Il modulo `subprocess` visto dal ricognitore: solo `run`, che è l'esploratore.

    Sostituito al posto del modulo vero dentro `supervision_scout`, così `git`,
    che passa dallo stesso nome, continua a girare per davvero.
    """

    TimeoutExpired = __import__("subprocess").TimeoutExpired

    def __init__(self, explorer: Explorer) -> None:
        self.explorer = explorer

    def run(self, command, **kwargs):
        return self.explorer(command, **kwargs)


def _install_explorer(monkeypatch, report: Path) -> Explorer:
    explorer = Explorer(report)
    monkeypatch.setattr(scout.shutil, "which", lambda name: "C:/finto/agy.exe" if name == "agy" else None)
    monkeypatch.setattr(scout, "subprocess", FakeSubprocess(explorer))
    monkeypatch.setattr(
        scout.windows, "close_tab",
        lambda task_id, role: {"line": f"scheda {role} chiusa (finta)"},
    )
    return explorer


def test_the_tab_runs_the_explorer_in_plan_mode_on_the_fixed_folder(task, monkeypatch, capsys):
    record = _opened(task)
    explorer = _install_explorer(monkeypatch, Path(record["report"]))

    assert scout.run_in_tab(task) == 0

    command = explorer.commands[0]
    assert command[0] == "C:/finto/agy.exe"
    assert command[1:3] == ["--mode", "plan"]
    assert "--prompt" in command
    prompt = command[command.index("--prompt") + 1]
    assert record["brief"] in prompt and record["report"] in prompt
    assert "Non modificare" in prompt
    done = [row for row in state.read_events(task) if row.get("event") == "scout_finished"][-1]
    assert done["round"] == record["round"]
    assert done["clean"] is True and done["report_written"] is True


def test_the_prompt_forbids_a_plan_and_asks_for_the_report_at_once(task, monkeypatch, capsys):
    """In `--mode plan`, con un brief grande, agy scriveva il piano, chiedeva "Proceed" e finiva
    senza rapporto. Il prompt deve dire di non pianificare, non chiedere conferma, e scrivere subito."""
    record = _opened(task)
    explorer = _install_explorer(monkeypatch, Path(record["report"]))

    assert scout.run_in_tab(task) == 0

    command = explorer.commands[0]
    prompt = command[command.index("--prompt") + 1].lower()
    assert "non scrivere un piano" in prompt
    assert "non chiedere conferma" in prompt
    assert "esegui" in prompt and "subito" in prompt
    assert record["report"].lower() in prompt


def test_a_missing_explorer_is_said_out_loud_and_journals_nothing(task, monkeypatch, capsys):
    _opened(task)
    monkeypatch.setattr(scout.shutil, "which", lambda name: None)

    assert scout.run_in_tab(task) == 1

    assert "agy" in capsys.readouterr().err
    assert not [row for row in state.read_events(task) if row.get("event") == "scout_finished"]


def test_a_tab_that_dirtied_the_copy_ends_in_failure(task, monkeypatch):
    record = _opened(task)
    explorer = _install_explorer(monkeypatch, Path(record["report"]))
    (Path(record["folder"]) / "README.md").write_text("# toccato\n", encoding="utf-8")

    assert scout.run_in_tab(task) == 1
    assert explorer.commands


def test_the_round_marks_its_tab_so_the_engine_can_find_it_later(task):
    """La scheda porta il nome che la chiude: senza, il motore dovrebbe indovinare."""
    opener = Opener()

    _opened(task, opener=opener)

    assert opener.last["marker"] == f"nexgen-sup:{task}:scout"


def test_the_round_closes_its_tab_when_it_ends(task, monkeypatch):
    _opened(task)
    _install_explorer(monkeypatch, Path(scout._opened(task)["report"]))
    closed: list = []
    monkeypatch.setattr(
        scout.windows, "close_tab",
        lambda task_id, role: closed.append((task_id, role)) or {"line": "chiusa"},
    )

    assert scout.run_in_tab(task) == 0

    assert closed == [(task, "scout")]


def test_the_tab_bootstrap_is_valid_python(task):
    compile(scout._bootstrap(task, 1), "<scheda>", "exec")


# --- il verbo ---------------------------------------------------------------


def test_the_verb_prepares_the_round_and_says_where_everything_is(task, monkeypatch, capsys):
    opener = Opener()
    monkeypatch.setattr(scout.windows, "open_window", opener)

    assert cli.main(["sup", "scout", task, "dove sta il confine?"]) == 0

    out = capsys.readouterr().out
    record = [row for row in state.read_events(task) if row.get("event") == "scout_opened"][-1]
    assert record["folder"] in out
    assert record["brief"] in out
    assert record["report"] in out
    assert "fiducia della cartella: aggiunta ora" in out


def test_the_verb_shows_the_tab_switches_only_to_who_asks_for_help_on_them(lane, capsys):
    with pytest.raises(SystemExit):
        cli.main(["sup", "scout", "--help"])

    out = capsys.readouterr().out
    assert "domanda" in out
    assert "--in-scheda" not in out and "--round" not in out


def test_the_tab_switch_runs_the_round_instead_of_preparing_a_new_one(task, monkeypatch):
    seen: list[tuple] = []
    monkeypatch.setattr(scout, "run_in_tab", lambda task_id, round_no=0: seen.append((task_id, round_no)) or 0)

    assert cli.main(["sup", "scout", task, "--in-scheda", "--round", "2"]) == 0

    assert seen == [(task, 2)]
