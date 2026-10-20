"""I verbi della Fase 2 dalla riga di comando: unit, collect e lo stato per unità.

Repository git veri e lane temporanea: nessuna finestra, nessun OpenCode.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from nexgen_local import cli
from nexgen_local import supervision_state as state

from supervision_repo import build, git_in

VERIFY = f'"{sys.executable}" -c "print(1)"'


@pytest.fixture
def lane(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "lane_state_dir", lambda: tmp_path / "lane")
    return tmp_path / "lane" / "supervision"


@pytest.fixture
def repo(tmp_path):
    return build(tmp_path / "sandbox")


@pytest.fixture
def brief(tmp_path):
    path = tmp_path / "brief.md"
    path.write_text("# Brief dell'unità prova\n\nSolo fatti.\n", encoding="utf-8")
    return str(path)


def _task(lane) -> str:
    return state.start("prova fase 2")["id"]


def test_unit_prints_the_copy_and_the_branch_it_prepared(lane, repo, brief, capsys):
    task = _task(lane)
    capsys.readouterr()

    assert cli.main([
        "sup", "unit", task, "prova",
        "--repo", str(repo.path), "--base", "main", "--brief", brief,
        "--allow", "docs/*.md", "--verify", VERIFY,
    ]) == 0

    out = capsys.readouterr().out
    assert f"ramo sup/{task}/prova" in out
    assert str(lane / task / "23-unita-prova") in out
    assert "push bloccata" in out


def test_unit_refuses_a_missing_brief(lane, repo, tmp_path, capsys):
    task = _task(lane)
    capsys.readouterr()

    assert cli.main([
        "sup", "unit", task, "prova",
        "--repo", str(repo.path), "--base", "main", "--brief", str(tmp_path / "manca.md"),
        "--allow", "docs/*.md",
    ]) == 1

    assert "brief inesistente" in capsys.readouterr().err


def test_collect_prints_the_gate_lines_and_exits_by_the_verdict(lane, repo, brief, capsys):
    task = _task(lane)
    cli.main([
        "sup", "unit", task, "prova",
        "--repo", str(repo.path), "--base", "main", "--brief", brief,
        "--allow", "docs/*.md", "--verify", VERIFY,
    ])
    folder = lane / task / "23-unita-prova"
    (folder / "docs" / "guida.md").write_text("# guida\n\nriga buona.\n", encoding="utf-8")
    git_in(folder, "add", "-A")
    git_in(folder, "commit", "-q", "-m", "docs: aggiunge una riga")
    capsys.readouterr()

    assert cli.main(["sup", "collect", task, "prova"]) == 0
    ok = capsys.readouterr().out
    assert "unità prova · verdetto PASS" in ok
    assert "PASS confine" in ok
    assert "pacchetto" in ok

    (folder / "fuori.txt").write_text("sporco\n", encoding="utf-8")
    assert cli.main(["sup", "collect", task, "prova"]) == 1
    bad = capsys.readouterr().out
    assert "verdetto FAIL" in bad
    assert "FAIL albero pulito" in bad


def test_status_shows_the_unit_its_state_and_its_verdict(lane, repo, brief, capsys):
    task = _task(lane)
    cli.main([
        "sup", "unit", task, "prova",
        "--repo", str(repo.path), "--base", "main", "--brief", brief,
        "--allow", "docs/*.md", "--verify", VERIFY,
    ])
    folder = lane / task / "23-unita-prova"
    (folder / "docs" / "guida.md").write_text("# guida\n\nriga.\n", encoding="utf-8")
    git_in(folder, "add", "-A")
    git_in(folder, "commit", "-q", "-m", "docs: aggiunge una riga")
    cli.main(["sup", "collect", task, "prova"])
    capsys.readouterr()

    assert cli.main(["sup", "status", task]) == 0

    out = capsys.readouterr().out
    assert "unità prova · esecutore · raccolta · PASS" in out
    assert "costo non disponibile" in out


def test_status_of_a_task_without_units_stays_as_before(lane, capsys):
    task = _task(lane)
    capsys.readouterr()

    assert cli.main(["sup", "status", task]) == 0

    assert "unità: nessuna agganciata" in capsys.readouterr().out
    assert Path(lane / task).is_dir()


def test_run_says_when_the_unit_waits_in_the_queue(lane, capsys, monkeypatch):
    from nexgen_local import supervision_harness as harness

    task = _task(lane)
    monkeypatch.setattr(harness, "run_unit", lambda task_id, unit: {
        "unit": unit, "mode": "queued", "session": "ses_occupata", "reason": "sessione occupata",
    })
    capsys.readouterr()

    assert cli.main(["sup", "run", task, "prova"]) == 0
    out = capsys.readouterr().out
    assert "in coda: sessione occupata sulla sessione ses_occupata" in out
    assert "una finestra nuova non si apre" in out


def test_run_reports_the_work_sent_into_the_existing_session(lane, capsys, monkeypatch):
    from nexgen_local import supervision_harness as harness

    task = _task(lane)
    monkeypatch.setattr(harness, "run_unit", lambda task_id, unit: {
        "unit": unit, "mode": "run", "session": "ses_libera", "pid": 4321,
        "row": {"cost_usd": 0.125},
    })
    capsys.readouterr()

    assert cli.main(["sup", "run", task, "prova"]) == 0
    out = capsys.readouterr().out
    assert "lavoro inviato alla sessione ses_libera (pid 4321), nessuna scheda nuova" in out
    assert "costo all'invio: 0.1250 $" in out
