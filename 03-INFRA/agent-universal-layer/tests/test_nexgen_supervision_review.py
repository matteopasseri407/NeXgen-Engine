"""Il giro di revisione: APPROVA solo su una raccolta PASS, RIMANDA che torna all'esecutore.

Repository git veri, sessione harness finta: nessun OpenCode, nessuna finestra.
Quello che si prova è il file del giro, il numero che cresce, il rifiuto quando
la head si è mossa, e che le correzioni arrivino nella sessione senza il loro
testo in una riga di comando.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from nexgen_local import cli
from nexgen_local import supervision_costs as costs
from nexgen_local import supervision_gates as gates
from nexgen_local import supervision_harness as harness
from nexgen_local import supervision_review as review
from nexgen_local import supervision_state as state
from nexgen_local import supervision_worktree as worktree

from supervision_repo import build, git_in

VERIFY_OK = f'"{sys.executable}" -c "print(1)"'
CORRECTIONS = (
    "# Corrections for the unit\n"
    "\n"
    "- the second paragraph names the wrong file: use `docs/guida.md`\n"
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


def _corrections(tmp_path, text: str = CORRECTIONS) -> Path:
    path = tmp_path / "correzioni.md"
    path.write_text(text, encoding="utf-8")
    return path


def _fake_session(task: str, session: str = "ses_libera", *, stopped: bool = True) -> list:
    """Bind a session to the task and make its state readable without OpenCode."""
    state.append_event(task, "attached", unit="prima", role="esecutore", harness="opencode", session=session)
    return [session, stopped]


def _idle(monkeypatch, session: list) -> list[tuple]:
    """A session that is idle answers `opencode run -s`; the call is captured, not launched."""
    sent: list[tuple] = []
    facts = costs.SessionFacts(
        created_ms=1_000, updated_ms=2_000, idle_ms=2_000 if session[1] else 0,
    )
    monkeypatch.setattr(harness.costs, "measure", lambda _session: facts)
    monkeypatch.setattr(
        harness, "_run_in_session",
        lambda session_id, prompt, folder: sent.append((session_id, prompt, folder)) or SimpleNamespace(pid=99),
    )
    monkeypatch.setattr(
        harness.costs, "record",
        lambda task_id, unit, role, session_id, **kwargs: {"session": session_id, "unit": unit, "cost_usd": 0.0},
    )
    return sent


def test_approve_writes_the_round_naming_the_commit_the_gates_passed(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path)
    head = _commit(folder, "docs: aggiunge una riga", {"docs/guida.md": "# guida\n\nriga nuova.\n"})
    collected = gates.collect(task, "prova")

    result = review.approve(task, "prova", "il diff fa una cosa sola e i cancelli sono verdi")

    assert result["round"] == 1
    assert result["head"] == head == collected["head"]
    assert result["collect_verdict"] == "PASS"
    path = Path(result["path"])
    assert path.name == "45-revisione-prova-1.md"
    text = path.read_text(encoding="utf-8")
    assert "verdetto: APPROVA" in text
    assert f"head: {head}" in text
    assert "raccolta: PASS" in text
    assert "## Motivo" in text
    assert "il diff fa una cosa sola" in text
    reviewed = [row for row in state.read_events(task) if row.get("event") == "unit_reviewed"]
    assert reviewed[-1]["verdict"] == state.APPROVED
    assert reviewed[-1]["round"] == 1
    assert reviewed[-1]["head"] == head
    assert state.status(task)["units"][0]["approved"] is True


def test_approve_is_refused_when_the_collection_said_fail(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path)
    _commit(folder, "docs: aggiunge una riga", {"docs/guida.md": "# guida\n\nriga.\n"})
    (folder / "fuori.txt").write_text("sporco\n", encoding="utf-8")
    assert gates.collect(task, "prova")["verdict"] == "FAIL"

    with pytest.raises(review.ReviewError, match="verdetto FAIL"):
        review.approve(task, "prova", "va bene lo stesso")

    assert not list(Path(state.open_task(task)).glob("45-revisione-*.md"))


def test_approve_is_refused_when_the_branch_moved_after_the_collection(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path)
    _commit(folder, "docs: aggiunge una riga", {"docs/guida.md": "# guida\n\nriga.\n"})
    collected = gates.collect(task, "prova")
    _commit(folder, "docs: un altro commit dopo la raccolta", {"docs/altra.md": "# altra\n"})

    with pytest.raises(review.ReviewError, match="cambiata dopo la raccolta"):
        review.approve(task, "prova", "il primo commit andava bene")

    assert collected["verdict"] == "PASS"


def test_approve_refuses_a_unit_the_engine_never_collected(task, repo, tmp_path):
    _unit(task, repo, tmp_path)

    with pytest.raises(review.ReviewError, match="mai raccolta"):
        review.approve(task, "prova", "fidati")


def test_approve_refuses_an_empty_reason(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path)
    _commit(folder, "docs: aggiunge una riga", {"docs/guida.md": "# guida\n\nriga.\n"})
    gates.collect(task, "prova")

    with pytest.raises(review.ReviewError, match="motivo vuoto"):
        review.approve(task, "prova", "   ")


def test_rework_writes_the_round_and_sends_a_pointer_into_the_idle_session(task, repo, tmp_path, monkeypatch):
    folder = _unit(task, repo, tmp_path)
    _commit(folder, "docs: aggiunge una riga", {"docs/guida.md": "# guida\n\nriga.\n"})
    gates.collect(task, "prova")
    sent = _idle(monkeypatch, _fake_session(task))

    result = review.rework(task, "prova", _corrections(tmp_path))

    assert result["round"] == 1
    path = Path(result["path"])
    assert path.name == "45-revisione-prova-1.md"
    assert "verdetto: RIMANDA" in path.read_text(encoding="utf-8")
    assert "## Correzioni" in path.read_text(encoding="utf-8")
    assert result["delivery"]["mode"] == "run"
    assert sent and sent[0][0] == "ses_libera"
    assert str(path) in sent[0][1], "il prompt punta al file di revisione"
    assert "names the wrong file" not in sent[0][1], "le correzioni non finiscono nella riga di comando"
    assert len(sent[0][1]) < 800
    events = [row for row in state.read_events(task) if row.get("event") == "unit_reworked"]
    assert events[-1]["round"] == 1
    assert events[-1]["session"] == "ses_libera"


def test_the_second_round_is_numbered_two_and_goes_back_to_the_idle_session(task, repo, tmp_path, monkeypatch):
    folder = _unit(task, repo, tmp_path)
    _commit(folder, "docs: aggiunge una riga", {"docs/guida.md": "# guida\n\nprima.\n"})
    gates.collect(task, "prova")
    sent = _idle(monkeypatch, _fake_session(task))
    first = review.rework(task, "prova", _corrections(tmp_path))
    _commit(folder, "docs: corregge la riga", {"docs/guida.md": "# guida\n\nseconda, corretta.\n"})
    gates.collect(task, "prova")

    second = review.rework(task, "prova", _corrections(tmp_path, CORRECTIONS + "- ancora una riga\n"))

    assert first["round"] == 1
    assert second["round"] == 2
    assert Path(second["path"]).name == "45-revisione-prova-2.md"
    assert Path(first["path"]).is_file(), "il giro 1 non si riscrive"
    assert len(sent) == 2, "ogni giro arriva nella stessa sessione"
    assert {session for session, _prompt, _folder in sent} == {"ses_libera"}
    rounds = [row["round"] for row in state.read_events(task) if row.get("event") == "unit_reworked"]
    assert rounds == [1, 2]
    assert state.status(task)["units"][0]["review_round"] == 2


def test_corrections_wait_in_the_queue_when_the_session_is_busy(task, repo, tmp_path, monkeypatch):
    folder = _unit(task, repo, tmp_path)
    _commit(folder, "docs: aggiunge una riga", {"docs/guida.md": "# guida\n\nriga.\n"})
    gates.collect(task, "prova")
    sent = _idle(monkeypatch, _fake_session(task, stopped=False))

    result = review.rework(task, "prova", _corrections(tmp_path))

    assert result["delivery"]["mode"] == "queued"
    assert result["delivery"]["reason"] == "sessione occupata"
    assert sent == [], "una sessione occupata non riceve le correzioni"
    queued = [row for row in state.read_events(task) if row.get("event") == "unit_queued"]
    assert queued[-1]["round"] == 1
    assert queued[-1]["session"] == "ses_libera"


def test_rework_refuses_a_missing_file_and_an_empty_one(task, repo, tmp_path):
    folder = _unit(task, repo, tmp_path)
    _commit(folder, "docs: aggiunge una riga", {"docs/guida.md": "# guida\n\nriga.\n"})
    gates.collect(task, "prova")

    with pytest.raises(review.ReviewError, match="inesistente"):
        review.rework(task, "prova", tmp_path / "manca.md")
    with pytest.raises(review.ReviewError, match="vuoto"):
        review.rework(task, "prova", _corrections(tmp_path, "   \n"))


def test_corrections_without_a_session_are_refused_instead_of_opening_a_window(task, repo, tmp_path, monkeypatch):
    folder = _unit(task, repo, tmp_path)
    _commit(folder, "docs: aggiunge una riga", {"docs/guida.md": "# guida\n\nriga.\n"})
    gates.collect(task, "prova")
    monkeypatch.setattr(harness.windows, "open_window", lambda *a, **k: pytest.fail("nessuna finestra"))
    monkeypatch.setattr(harness, "_run_in_session", lambda *a: pytest.fail("nessuna sessione da raggiungere"))

    with pytest.raises(harness.HarnessError, match="nessuna sessione harness"):
        review.rework(task, "prova", _corrections(tmp_path))

    assert (Path(state.open_task(task)) / "45-revisione-prova-1.md").is_file(), \
        "le correzioni restano scritte anche quando la consegna non riesce"


def test_the_cli_prints_the_round_and_the_verdict(task, repo, tmp_path, capsys, monkeypatch):
    folder = _unit(task, repo, tmp_path)
    _commit(folder, "docs: aggiunge una riga", {"docs/guida.md": "# guida\n\nriga.\n"})
    gates.collect(task, "prova")
    capsys.readouterr()

    assert cli.main(["sup", "review", task, "prova", "--approve", "una riga sola e verde"]) == 0

    out = capsys.readouterr().out
    assert f"unità prova · giro 1 · {state.APPROVED}" in out
    assert "45-revisione-prova-1.md" in out
    assert "raccolta PASS sulla stessa head" in out
    assert "pronta per `sup publish`" in out


def test_the_cli_says_why_it_refused(task, repo, tmp_path, capsys):
    _unit(task, repo, tmp_path)
    capsys.readouterr()

    assert cli.main(["sup", "review", task, "prova", "--approve", "senza raccolta"]) == 1

    assert "mai raccolta" in capsys.readouterr().err
