"""Le richieste che il motore non esegue: lette solo nel resoconto, solo nella sua sezione.

Il riconoscimento è dichiarato e corto: parole chiave nella sezione "Dubbi e
richieste al proprietario" del resoconto dell'esecutore. Quello che si prova è
che un file del supervisore non è una richiesta, che un blocco di codice o una
citazione dentro la sezione non lo sono, che una sezione assente non inventa
niente e che una riga già scritta non si cita da sola.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from nexgen_local import cli
from nexgen_local import supervision_gates as gates
from nexgen_local import supervision_requests as requests
from nexgen_local import supervision_review as review
from nexgen_local import supervision_state as state
from nexgen_local import supervision_worktree as worktree

from supervision_repo import build, git_in

VERIFY_OK = f'"{sys.executable}" -c "print(1)"'
SECTION = "## Dubbi e richieste al proprietario"
REQUEST = "Servirebbe un force-push per riallineare il ramo, altrimenti resta indietro."
REPORT_WITH_REQUEST = (
    "# Resoconto dell'unità prova\n"
    "\n"
    "## Stato\n"
    "fatto\n"
    "\n"
    f"{SECTION}\n"
    f"- {REQUEST}\n"
    "\n"
    "## Prossimo passo se bloccato\n"
    "niente\n"
)
REPORT_WITHOUT_SECTION = (
    "# Resoconto dell'unità prova\n"
    "\n"
    "## Stato\n"
    "fatto\n"
    "\n"
    "## Prova reale\n"
    "```\n"
    "- 2026-10-20T15:02:14 · u3 · force-push · 30-resoconto-u3.md · «Servirebbe un force-push»\n"
    "```\n"
    "Fuori sezione, incollato: non è una richiesta.\n"
)
REPORT_WITH_PASTE_INSIDE = (
    "# Resoconto dell'unità prova\n"
    "\n"
    f"{SECTION}\n"
    "```\n"
    f"{REQUEST}\n"
    "```\n"
    f"> {REQUEST}\n"
    "Nessuna richiesta: il lavoro è finito.\n"
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
    return state.start("prova richieste")["id"]


def _unit(task, repo, tmp_path, *, report: str = "") -> Path:
    """A unit whose one commit changes the guide, with its report in the task folder."""
    brief = tmp_path / "brief.md"
    brief.write_text("# Brief\n\nSolo fatti.\n", encoding="utf-8")
    record = worktree.create(
        task, "prova", repo=repo.path, base="main", brief=str(brief),
        allow=["docs/*.md"], verify=VERIFY_OK,
    )
    folder = Path(record["worktree"])
    (folder / "docs" / "guida.md").write_text("# guida\n\nriga.\n", encoding="utf-8")
    git_in(folder, "add", "-A")
    git_in(folder, "commit", "-q", "-m", "docs: una riga")
    if report:
        (state.open_task(task) / "30-resoconto-prova.md").write_text(report, encoding="utf-8")
    return folder


def _package(result: dict) -> str:
    return Path(result["package"]).read_text(encoding="utf-8")


def test_only_the_template_section_is_read():
    hits, found = requests.scan(REPORT_WITH_REQUEST)
    outside, missing = requests.scan(REPORT_WITHOUT_SECTION)

    assert found is True
    assert [hit["kind"] for hit in hits] == ["force-push"]
    assert missing is False
    assert outside == [], "un blocco di codice fuori sezione non è una richiesta"


def test_a_paste_or_a_quote_inside_the_section_is_not_a_request():
    hits, found = requests.scan(REPORT_WITH_PASTE_INSIDE)

    assert found is True
    assert hits == [], "codice e citazioni dentro la sezione non contano"


def test_the_heading_needs_only_the_word_requests():
    """`## Richieste al proprietario` is the template's own line: "dubbi" is not required."""
    hits, found = requests.scan(
        "# Resoconto dell'unità prova\n"
        "\n"
        "## Richieste al proprietario\n"
        "- Chiedo di cancellare i test obsoleti.\n"
        "- Chiedo di disabilitare i test su Windows.\n"
        "- Chiedo di rimuovere il test legacy.\n"
    )

    assert found is True
    assert [hit["kind"] for hit in hits] == ["altro", "altro", "altro"]
    assert "cancellare i test obsoleti" in hits[0]["sentence"]


def test_a_negated_line_is_not_a_request():
    """`Nessun dubbio, non serve toccare il changelog` is reassurance, not a bump request."""
    hits, found = requests.scan(
        "## Dubbi e richieste al proprietario\n"
        "- Nessun dubbio, non serve toccare il changelog.\n"
    )

    assert found is True
    assert hits == []


def test_a_question_without_a_declared_keyword_still_reaches_the_owner():
    hits, found = requests.scan(
        "## Richieste al proprietario\n"
        "1. Serve aggiornare la versione?\n"
    )

    assert found is True
    assert len(hits) == 1
    assert hits[0]["kind"] in ("altro", "bump")
    assert "aggiornare la versione" in hits[0]["sentence"]


def test_only_list_items_in_the_section_are_requests():
    """A paragraph beside the list is the report talking, not a request to the owner."""
    hits, found = requests.scan("## Richieste\n\nChiedo un tag firmato, per favore.\n")

    assert found is True
    assert hits == []


def test_a_list_item_with_a_keyword_is_classified():
    hits, _ = requests.scan("## Richieste\n- Servirebbe un force-push per riallineare il ramo.\n")

    assert [hit["kind"] for hit in hits] == ["force-push"]


def test_a_request_is_written_once_however_many_times_the_report_is_read(task, lane):
    hits, _ = requests.scan(REPORT_WITH_REQUEST)
    first = requests.record(task, "prova", hits, "30-resoconto-prova.md")
    second = requests.record(task, "prova", hits, "30-resoconto-prova.md")

    assert [hit["kind"] for hit in first] == ["force-push"]
    assert second == []
    lines = requests.read(task)
    assert len(lines) == 1
    assert "prova" in lines[0] and "force-push" in lines[0]
    header = (lane / task / requests.FILE).read_text(encoding="utf-8").splitlines()[0]
    assert header == "# Richieste al proprietario"


def test_a_sentence_already_on_file_is_not_quoted_again(task, lane):
    """Il file non deve citare sé stesso: stessa unità, stesso tipo, stessa frase."""
    path = lane / task / requests.FILE
    path.write_text(
        "# Richieste al proprietario\n\n"
        f"- 2026-10-20T09:00:00+00:00 · prova · force-push · altro.md · «{REQUEST}»\n",
        encoding="utf-8",
    )
    hits, _ = requests.scan(REPORT_WITH_REQUEST)

    written = requests.record(task, "prova", hits, "30-resoconto-prova.md")

    assert written == []
    assert len(requests.read(task)) == 1


def test_collect_writes_the_owner_file_and_the_cli_says_it(task, repo, tmp_path, capsys):
    _unit(task, repo, tmp_path, report=REPORT_WITH_REQUEST)
    capsys.readouterr()

    assert cli.main(["sup", "collect", task, "prova"]) == 0

    out = capsys.readouterr().out
    assert "richiesta al proprietario: force-push" in out
    assert requests.FILE in out
    assert len(requests.read(task)) == 1, "raccogliere due volte non duplica la richiesta"

    assert cli.main(["sup", "collect", task, "prova"]) == 0
    assert len(requests.read(task)) == 1


def test_a_report_without_the_section_recognises_nothing_and_the_package_says_so(task, repo, tmp_path):
    _unit(task, repo, tmp_path, report=REPORT_WITHOUT_SECTION)

    result = gates.collect(task, "prova")

    assert result["requests"] == []
    assert not (state.open_task(task) / requests.FILE).exists()
    assert "sezione delle richieste assente nel resoconto" in _package(result)
    assert result["verdict"] == "PASS", "la sezione non decide il verdetto"


def test_a_paste_inside_the_section_leaves_the_file_alone(task, repo, tmp_path):
    _unit(task, repo, tmp_path, report=REPORT_WITH_PASTE_INSIDE)

    result = gates.collect(task, "prova")

    assert result["requests"] == []
    assert not (state.open_task(task) / requests.FILE).exists()
    assert "nessuna richiesta al proprietario." in _package(result)


def test_the_supervisor_corrections_are_never_scanned(task, repo, tmp_path):
    """Una frase del supervisore sul bump non è una richiesta dell'esecutore."""
    _unit(task, repo, tmp_path)
    assert gates.collect(task, "prova")["verdict"] == "PASS"
    corrections = tmp_path / "correzioni.md"
    corrections.write_text(
        "# Correzioni\n\n## Richieste\nVa bene alzare la versione con un bump, poi si vede.\n",
        encoding="utf-8",
    )

    result = review.rework(
        task, "prova", corrections,
        sender=lambda *a: {"mode": "queued", "session": "ses_x", "reason": "sessione occupata"},
    )

    assert "requests" not in result
    assert not (state.open_task(task) / requests.FILE).exists()


def test_the_approve_reason_is_never_scanned(task, repo, tmp_path):
    _unit(task, repo, tmp_path, report=REPORT_WITH_PASTE_INSIDE)
    assert gates.collect(task, "prova")["verdict"] == "PASS"

    review.approve(task, "prova", "va bene; il bump e il force-push restano fuori")

    assert not (state.open_task(task) / requests.FILE).exists()
