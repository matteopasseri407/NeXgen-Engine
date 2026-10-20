"""Le richieste che il motore non esegue: riconosciute, scritte, mai soddisfatte.

Il riconoscimento è dichiarato e corto: parole chiave nella sezione delle
richieste del resoconto. Quello che si prova è che una richiesta finisce nel file
del proprietario una volta sola, e che la lista delle cose vietate non è una
richiesta.
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
REPORT_WITH_REQUEST = (
    "# Resoconto dell'unità prova\n"
    "\n"
    "## Stato\n"
    "fatto\n"
    "\n"
    "## Dubbi e richieste al proprietario\n"
    "Servirebbe un force-push per riallineare il ramo, altrimenti resta indietro.\n"
    "\n"
    "## Prossimo passo se bloccato\n"
    "niente\n"
)
REPORT_WITH_PROHIBITION_ONLY = (
    "# Resoconto dell'unità prova\n"
    "\n"
    "## Cosa non fare\n"
    "Niente force-push, niente tag, niente bump di VERSION o CHANGELOG.md.\n"
    "\n"
    "## Dubbi e richieste al proprietario\n"
    "Nessuna: il lavoro è finito.\n"
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


def test_the_request_section_is_where_a_request_is_looked_for():
    assert [hit["kind"] for hit in requests.scan(REPORT_WITH_REQUEST)] == ["force-push"]
    assert requests.scan(REPORT_WITH_PROHIBITION_ONLY) == [], "la lista dei divieti non è una richiesta"


def test_a_request_is_written_once_however_many_times_the_report_is_read(task, lane):
    first = requests.record(task, "prova", "30-resoconto-prova.md", REPORT_WITH_REQUEST)
    second = requests.record(task, "prova", "30-resoconto-prova.md", REPORT_WITH_REQUEST)

    assert [hit["kind"] for hit in first] == ["force-push"]
    assert second == []
    lines = requests.read(task)
    assert len(lines) == 1
    assert "prova" in lines[0] and "force-push" in lines[0]
    assert "30-resoconto-prova.md" in lines[0]
    header = (lane / task / requests.FILE).read_text(encoding="utf-8").splitlines()[0]
    assert header == "# Richieste al proprietario"


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


def test_a_request_in_the_corrections_lands_in_the_owner_file(task, repo, tmp_path):
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

    assert [hit["kind"] for hit in result["requests"]] == ["bump"]
    assert len(requests.read(task)) == 1
