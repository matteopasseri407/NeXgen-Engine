"""La finestra dell'esecutore e la sessione che nasce lì: nessun OpenCode vero, nessuna finestra vera.

Quello che si prova è l'argv che il motore costruisce, il fatto che il brief non
finisca in una riga di comando, e la ricerca della sessione per cartella e ora.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from nexgen_local import supervision_harness as harness
from nexgen_local import supervision_state as state


@pytest.fixture
def lane(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "lane_state_dir", lambda: tmp_path / "lane")
    return tmp_path / "lane" / "supervision"


@pytest.fixture
def task(lane):
    return state.start("prova fase 2")["id"]


@pytest.fixture
def unit(task, lane, tmp_path):
    """La copia e il brief di un'unità, senza git: la finestra non ne ha bisogno."""
    folder = lane / task / "23-unita-prova"
    folder.mkdir(parents=True)
    (lane / task / "20-brief-prova.md").write_text(
        "# Brief\n\ncontenuto segreto del brief\n", encoding="utf-8",
    )
    state.append_event(
        task, "unit_created", unit="prova", worktree=str(folder), branch=f"sup/{task}/prova",
        base="0" * 40, allow=["docs/*.md"], verify="", repo=str(tmp_path),
    )
    return folder


def test_run_opens_the_harness_on_the_unit_and_attaches_its_session(task, unit, monkeypatch):
    opened: dict = {}

    def fake_open(title, argv, *, cwd):
        opened.update({"title": title, "argv": list(argv), "cwd": cwd})
        return {"title": title, "launcher": "finto", "pid": 4242}

    recorded: dict = {}

    def fake_record(task_id, unit_name, role, session, **kwargs):
        recorded.update({"task": task_id, "unit": unit_name, "role": role, "session": session, **kwargs})
        return {"session": session, "unit": unit_name, "cost_usd": 0.5, "id": session}

    monkeypatch.setattr(harness, "_opencode_executable", lambda: "opencode-finto")
    monkeypatch.setattr(harness.costs, "record", fake_record)
    sessions = [{"id": "ses_nuova", "directory": str(unit), "created": 10**13}]

    result = harness.run_unit(task, "prova", opener=fake_open, lister=lambda: sessions, wait_s=0)

    assert opened["argv"][0] == "opencode-finto"
    assert opened["argv"][1] == str(unit)
    assert opened["argv"][2] == "--prompt"
    assert "--auto" not in opened["argv"], "nessuna auto-approvazione, mai"
    prompt = opened["argv"][3]
    assert str(unit.parent / "20-brief-prova.md") in prompt, "il prompt punta al brief"
    assert "contenuto segreto del brief" not in prompt, "il brief non finisce in una riga di comando"
    assert len(prompt) < 800, "un prompt breve che punta al brief, non il brief"
    assert opened["cwd"] == unit
    assert result["session"]["id"] == "ses_nuova"
    assert recorded["session"] == "ses_nuova"
    assert recorded["role"] == "esecutore"
    assert [row["event"] for row in state.read_events(task)] == [
        "started", "unit_created", "unit_started", "attached",
    ]


def test_run_says_so_when_no_session_can_be_told_apart(task, unit, monkeypatch):
    monkeypatch.setattr(harness, "_opencode_executable", lambda: "opencode-finto")
    monkeypatch.setattr(harness.costs, "record", lambda *a, **k: pytest.fail("non deve agganciare niente"))

    result = harness.run_unit(
        task, "prova",
        opener=lambda title, argv, *, cwd: {"title": title, "launcher": "finto", "pid": 1},
        lister=lambda: [],
        wait_s=0,
    )

    assert result["session"] is None
    assert [row["event"] for row in state.read_events(task)] == ["started", "unit_created", "unit_started"]


def test_run_refuses_a_unit_whose_copy_is_gone(task, unit, monkeypatch):
    harness_unit = unit
    harness_unit.rmdir()
    with pytest.raises(harness.HarnessError, match="copia dell'unità sparita"):
        harness.run_unit(task, "prova", opener=lambda *a, **k: None, lister=lambda: [], wait_s=0)


def test_find_session_takes_the_newest_session_in_that_folder(tmp_path):
    folder = tmp_path / "copia"
    folder.mkdir()
    sessions = [
        {"id": "ses_nuova", "directory": str(folder), "created": 2_000_000},
        {"id": "ses_vecchia", "directory": str(folder), "created": 1_000_000},
        {"id": "ses_altrove", "directory": str(tmp_path / "altrove"), "created": 3_000_000},
    ]

    found = harness.find_session(folder, 1_500_000, wait_s=0, lister=lambda: sessions)

    assert found["id"] == "ses_nuova"


def test_find_session_ignores_a_session_older_than_the_window(tmp_path):
    folder = tmp_path / "copia"
    folder.mkdir()
    sessions = [{"id": "ses_vecchia", "directory": str(folder), "created": 1_000_000}]

    assert harness.find_session(folder, 2_000_000, wait_s=0, lister=lambda: sessions) is None


def test_find_session_waits_for_the_session_to_appear(tmp_path):
    folder = tmp_path / "copia"
    folder.mkdir()
    answers = [[], [{"id": "ses_tardi", "directory": str(folder), "created": 5_000_000}]]
    times = iter([0.0, 0.0, 1.0, 2.0, 3.0])

    found = harness.find_session(
        folder, 1_000_000, wait_s=10, poll_s=1, lister=lambda: answers.pop(0) if answers else [],
        clock=lambda: next(times), sleep=lambda seconds: None,
    )

    assert found["id"] == "ses_tardi"


def test_find_session_gives_up_when_nothing_appears(tmp_path):
    folder = tmp_path / "copia"
    folder.mkdir()
    times = iter([0.0, 5.0])

    assert harness.find_session(
        folder, 1_000_000, wait_s=5, poll_s=1, lister=lambda: [], clock=lambda: next(times),
        sleep=lambda seconds: None,
    ) is None


def test_the_prompt_names_where_the_report_goes():
    brief = Path("/tmp/compito/20-brief-prova.md")
    prompt = harness.prompt_text("compito", "prova", brief)
    assert "30-resoconto-prova.md" in prompt
    assert "Niente push" in prompt
